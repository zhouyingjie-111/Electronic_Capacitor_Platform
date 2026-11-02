/** 智能对话界面：模型选择、流式事件、附件和历史管理；Markdown 展示必须经过现有净化流程，禁止直接注入模型 HTML。 */
async function chatUpgradeArchive(action,value){
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open('pe-chat-upgrade-v1',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('archives',{keyPath:'id'});
    request.onerror=()=>reject(new Error('旧对话备份无法写入浏览器。'));
    request.onsuccess=()=>{const db=request.result,tx=db.transaction('archives',action==='list'?'readonly':'readwrite'),store=tx.objectStore('archives');
      const operation=action==='list'?store.getAll():action==='save'?store.put(value):store.delete(value);let result;
      operation.onsuccess=()=>{result=operation.result;};tx.oncomplete=()=>{db.close();resolve(result);};tx.onerror=()=>{db.close();reject(new Error('旧对话备份处理失败。'));};};
  });
}
async function backupLegacyChat(){
  const get=async path=>{const r=await fetch('/api/project/ai/'+path,{cache:'no-store'});if(!r.ok)throw new Error('旧对话读取失败，请先导出当前对话。');return r.json();};
  let count=0;
  for(const scope of ['general','tracking','capacitance']){
    const state=await get('status?scope='+scope);if(!state.ready)continue;
    for(const item of state.threads||[]){
      const thread=await get('threads/'+item.id);if(!thread.messages.length)continue;
      const files=[];const ids=new Set(thread.messages.flatMap(m=>(m.attachments||[]).map(a=>a.id)));
      for(const id of ids){const meta=thread.messages.flatMap(m=>m.attachments||[]).find(a=>a.id===id);const r=await fetch('/api/project/ai/attachments/'+id);if(!r.ok)throw new Error('旧附件备份失败，请先下载保留。');files.push({id,name:meta.name,raw:await r.arrayBuffer()});}
      await chatUpgradeArchive('save',{id:state.provider+'/'+state.selected.id+'/'+item.id,provider:state.provider,model:state.selected,scope,title:thread.title,messages:thread.messages,files,saved_at:Date.now()});count++;
    }
  }
  return count;
}
document.addEventListener('DOMContentLoaded', async () => {
  const root=document.querySelector('[data-ai-scope]');
  if(!root)return;
  if(!document.getElementById('ai-storage-state')){
    try{const count=await backupLegacyChat();root.dataset.legacyBackup='completed';document.getElementById('ai-connection-state').textContent='更新前对话已备份至本机浏览器（'+count+'个），更新完成后可恢复。';}
    catch(error){root.dataset.legacyBackup='failed';document.getElementById('ai-connection-state').textContent=error.message;}
    return;
  }
  const scope=root.dataset.aiScope, $=id=>document.getElementById('ai-'+id);
  const titles={general:'电力电子专家',tracking:'智能控制助手',capacitance:'电容辨识助手'};
  const suggestions={general:['计算 LC 谐振频率并说明适用条件','搭建一个 Buck 平均模型','打开 Simulink 空白模型'],tracking:['DTgiven 与实际 PWM 占空比有什么区别？','如何公平比较 PI 与 ANN 的跟踪效果？','准备 22 A、50 μs、0.01 s 的 PI 仿真'],capacitance:['如何从三路信号构建 Haar 小波特征？','容值与 ESR 的输出顺序和单位是什么？','如何设计可变位置电容的辨识精度实验？']};
  let status=null,thread=null,pending=[],busy=false,uploading=0,jobId=null,streamAbort=null,ready=false;
  let rendered=new Map(),autoFollow=true,models=[],renderTimer=null;
  const historyChannel=window.BroadcastChannel?new BroadcastChannel('pe-chat-history-v1'):null;
  const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
  const feedback=(text,error=false,auth=false)=>{const node=$(auth?'auth-feedback':'chat-feedback');node.textContent=text;node.classList.toggle('is-error',error);};
  async function api(path,body,method){
    const options=body===undefined?{cache:'no-store'}:{method:method||'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
    const response=await fetch('/api/project/ai/'+path,options);
    let result;try{result=await response.json();}catch{throw new Error('本地服务返回异常，请刷新后重试。');}
    if(!response.ok){
      if(response.status===401&&!['connect','select'].includes(path)){ready=false;status=null;thread=null;pending=[];renderPending();showGate(1);feedback('会话已过期，请重新输入 API Key。',true,true);}
      throw new Error(result.error||'请求未完成。');
    }
    return result;
  }
  function setBusy(value){
    busy=value;
    ['send','new-thread','clear','history','settings'].forEach(id=>{$(id).disabled=value;});
    $('send').hidden=value;$('stop').hidden=!value;$('stop').disabled=false;
    $('add-file').disabled=value||uploading>0;
    root.querySelectorAll('.ai-retry,.ai-save-answer').forEach(button=>{button.disabled=value;});
  }
  function endScroll(){if(autoFollow)$('messages').scrollTop=$('messages').scrollHeight;$('follow-latest').hidden=autoFollow;}
  $('messages').addEventListener('scroll',()=>{const n=$('messages');autoFollow=n.scrollHeight-n.scrollTop-n.clientHeight<45;$('follow-latest').hidden=autoFollow;});
  $('messages').addEventListener('wheel',event=>{if(event.deltaY<0){autoFollow=false;$('follow-latest').hidden=false;}},{passive:true});
  $('follow-latest').addEventListener('click',()=>{autoFollow=true;endScroll();});
  function savedState(value){const node=$('storage-state');node.classList.toggle('is-error',Boolean(value.storage_error));node.textContent=value.storage_error||(value.saved_at?'已自动保存 · '+new Date(value.saved_at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'对话自动保存在本机');}
  async function copy(text,button){try{await navigator.clipboard.writeText(text);const old=button.textContent;button.textContent='已复制';setTimeout(()=>{button.textContent=old;},1200);}catch{feedback('复制未成功，请手动选中内容复制。',true);}}
  if(window.marked && window.katex){
    const math=(text,display)=>{try{return katex.renderToString(text,{displayMode:display,throwOnError:false,trust:false,maxExpand:300,maxSize:20,strict:'ignore'});}catch{return escape(text);}};
    marked.use({renderer:{html(token){return escape(token.text);},image(token){return escape('[图片链接：'+(token.text||'图片')+']');}},extensions:[
      {name:'displayMath',level:'block',start:src=>{const n=src.search(/\$\$|\\\[/);return n<0?undefined:n;},tokenizer(src){const match=/^\$\$\s*\n?([\s\S]+?)\n?\$\$(?:\n|$)/.exec(src)||/^\\\[([\s\S]+?)\\\]/.exec(src);if(match)return {type:'displayMath',raw:match[0],text:match[1]};},renderer:token=>math(token.text,true)},
      {name:'inlineMath',level:'inline',start:src=>{const n=src.search(/\$|\\\(/);return n<0?undefined:n;},tokenizer(src){const match=/^\$([^$\n]+?)\$(?!\$)/.exec(src)||/^\\\(([\s\S]+?)\\\)/.exec(src);if(match)return {type:'inlineMath',raw:match[0],text:match[1]};},renderer:token=>math(token.text,false)}
    ]});
  }
  function markdown(node,text){
    if(!window.marked||!window.DOMPurify){node.textContent=text;return;}
    let html;try{html=marked.parse(text,{gfm:true,breaks:false,async:false});}catch{node.textContent=text;return;}
    node.innerHTML=DOMPurify.sanitize(html,{FORBID_TAGS:['script','style','iframe','object','embed','form','input','img'],FORBID_ATTR:['id','name']});
    node.querySelectorAll('a').forEach(link=>{if(link.origin===location.origin&&/^\/api\/project\/ai\/files\/[a-f0-9]{32}\/download$/.test(link.pathname)){link.download='';}else{link.target='_blank';link.rel='noopener noreferrer';}});
    node.querySelectorAll('pre').forEach(pre=>{const code=pre.querySelector('code');if(!code)return;const button=element('button','ai-code-copy','复制代码');button.type='button';button.addEventListener('click',()=>copy(code.textContent,button));pre.append(button);});
    node.querySelectorAll('table').forEach(table=>{const wrapper=element('div','ai-table-scroll');table.replaceWith(wrapper);wrapper.append(table);});
  }
  function attachmentCard(item,removable=false){
    const card=element('div','ai-attachment-card');
    if(item.kind==='image'){const image=element('img');image.src='/api/project/ai/attachments/'+item.id;image.alt=item.name;card.append(image);}
    const info=element('div');const title=element('b');const link=element('a','',item.name);
    link.href='/api/project/ai/attachments/'+item.id;link.download=item.name;title.append(link);info.append(title);
    info.append(element('small','',(item.size/1024).toFixed(1)+' KB'+(item.note?' · '+item.note:'')));card.append(info);
    if(removable){const remove=element('button','','×');remove.type='button';remove.setAttribute('aria-label','移除 '+item.name);remove.addEventListener('click',async()=>{pending=pending.filter(p=>p.id!==item.id);renderPending();try{await api('attachments/'+item.id,{},'DELETE');}catch(error){feedback(error.message,true);}});card.append(remove);}
    return card;
  }
  function renderPending(){const node=$('attachments');node.replaceChildren(...pending.map(item=>attachmentCard(item,true)));$('send').disabled=busy||uploading>0;}
  function renderEmpty(){
    const node=$('messages');node.replaceChildren();rendered.clear();
    const empty=element('div','ai-empty');empty.append(element('strong','',titles[scope]),element('p','',scope==='general'?'从一个电路、一道计算或一份资料开始。':'围绕当前研究对象，结合数据、模型与实验条件展开交流。'));node.append(empty);
    $('suggestions').hidden=false;
  }
  function createMessage(message){
    const article=element('article','ai-message '+(message.role==='user'?'ai-user':'ai-assistant'));
    article.dataset.messageId=message.id;
    const meta=element('div','ai-message-meta');meta.append(element('strong','',message.role==='user'?'你':titles[scope]));
    if(message.role!=='user')meta.append(element('span','',message.model||status?.selected?.name||''));article.append(meta);
    if(message.role==='user'){
      if(message.content)article.append(element('div','ai-user-text',message.content));
      if(message.attachments?.length){const cards=element('div','ai-attachment-cards');message.attachments.forEach(item=>cards.append(attachmentCard(item)));article.append(cards);}
      if(message.shared_data)article.append(element('div','ai-message-note','本轮附带监测快照'));
      const actions=element('div','ai-message-actions'),button=element('button','','复制');button.type='button';button.addEventListener('click',()=>copy(message.content,button));actions.append(button);article.append(actions);
      return {article,message};
    }
    const reasoning=element('details','ai-reasoning');reasoning.hidden=true;
    reasoning.append(element('summary','','推理内容 / 摘要 · 服务商返回'));const reasonText=element('pre');reasoning.append(reasonText);
    const tools=element('div','ai-tool-results'),answer=element('div','ai-answer'),note=element('div','ai-message-note'),error=element('div','ai-message-error');
    article.append(reasoning,tools,answer,note,error);
    const actions=element('div','ai-message-actions'),copyButton=element('button','','复制回答'),retry=element('button','ai-retry','重新生成'),usage=element('span');
    copyButton.type=retry.type='button';copyButton.addEventListener('click',()=>copy(message.content,copyButton));retry.textContent='重新生成最新回复';retry.addEventListener('click',()=>send(true));retry.disabled=busy;
    const formats=element('select');formats.setAttribute('aria-label','回答下载格式');[['md','Markdown'],['docx','Word'],['pdf','PDF'],['txt','纯文本']].forEach(([value,label])=>formats.add(new Option(label,value)));
    const save=element('button','ai-save-answer','下载回答');save.type='button';save.disabled=busy;
    save.addEventListener('click',async()=>{save.disabled=true;try{const file=await api('threads/'+thread.id+'/messages/'+message.id+'/file',{format:formats.value});message.downloads=message.downloads||[];message.downloads.push(file);renderAssistant(view);const link=element('a');link.href=file.url;link.download=file.name;link.click();}catch(error){feedback(error.message,true);}finally{save.disabled=busy;}});
    actions.append(copyButton,retry,formats,save,usage);article.append(actions);
    const downloads=element('div','ai-answer-downloads');article.append(downloads);
    const view={article,message,reasoning,reasonText,tools,answer,note,error,usage,downloads,renderedTools:0,renderedDownloads:0};renderAssistant(view);return view;
  }
  function appendMessage(message){
    $('messages').querySelector('.ai-empty')?.remove();$('suggestions').hidden=true;
    const view=createMessage(message);rendered.set(message.id,view);$('messages').append(view.article);endScroll();return view;
  }
  function renderAssistant(view){
    const m=view.message;
    if(m.reasoning){view.reasoning.hidden=false;view.reasonText.textContent=m.reasoning;}
    markdown(view.answer,m.content||'');
    view.error.textContent=m.error||'';
    view.note.textContent=m.notice||(m.state==='responding'&&!m.content?'正在生成…':m.state==='stopped'?'已停止生成。':'');
    if(m.usage)view.usage.textContent='输入 '+(m.usage.prompt_tokens??'—')+' · 输出 '+(m.usage.completion_tokens??'—')+' tokens';
    for(const card of (m.tools||[]).slice(view.renderedTools)){view.tools.append(toolCard(card));view.renderedTools++;}
    const calcErrors=[...view.tools.querySelectorAll('[data-calculator-error]')];
    calcErrors.forEach((node,index)=>{node.hidden=index!==calcErrors.length-1;});
    for(const file of (m.downloads||[]).slice(view.renderedDownloads)){view.downloads.append(fileCard(file));view.renderedDownloads++;}
    endScroll();
  }
  function scheduleRender(view){if(renderTimer)return;renderTimer=setTimeout(()=>{renderTimer=null;renderAssistant(view);},70);}
  const toolNames={prepare_model_build:'Simulink 自动建模',create_download_file:'文件生成',engineering_calculator:'工程计算 · Python',analyze_uploaded_csv:'数据分析 · Python',inspect_project_model:'Simulink 模型检查',open_project_model:'打开 Simulink 模型',prepare_simulation:'Simulink 仿真方案',get_simulation_result:'仿真结果读取'};
  function fileCard(file){
    const node=element('div','ai-file-card'),info=element('div');info.append(element('strong','',file.name),element('small','',(file.size<1024?file.size+' B':(file.size/1024).toFixed(1)+' KB')+' · 已保存到本机'+(file.note?' · '+file.note:'')));node.append(info);
    const link=element('a','btn','下载文件');link.href='/api/project/ai/files/'+file.id+'/download';link.download=file.name;link.setAttribute('aria-label','下载文件 '+file.name);node.append(link);return node;
  }
  function toolCard(card){
    if(card.result.draft?.action==='build_model')return modelBuildCard(card.result.draft);
    if(card.result.draft&&(card.name==='open_project_model'||card.result.draft.action==='open_model'))return softwareOpenCard(card.result.draft);
    if(card.name==='engineering_calculator'&&card.result.error){
      const details=element('details','ai-tool-warning');details.dataset.calculatorError='true';
      details.append(element('summary','','计算表达式需要调整'));details.append(element('p','',card.result.error));return details;
    }
    const node=element('section','ai-tool-card');node.append(element('h4','',toolNames[card.name]||'本机工具'));
    const result=card.result;
    if(result.error){node.append(element('div','ai-message-error',result.error));return node;}
    if(result.file){node.append(fileCard(result.file));}
    else if(result.draft){
      const d=result.draft,label=d.label+' · '+d.duration+' s';node.append(element('div','ai-tool-state',label+(d.job_id?' · 已提交':' · 尚未运行')));
      const params=element('dl');Object.entries(d.parameters||{}).forEach(([key,value])=>{const row=element('div');row.append(element('dt','',key),element('dd','',String(value)));params.append(row);});node.append(params);
      const button=element('button','btn','运行此仿真');button.type='button';node.append(button);
      if(d.job_id){button.remove();setTimeout(()=>monitorSimulation(node,d.job_id),0);}
      button.addEventListener('click',async()=>{button.disabled=true;try{const job=await api('drafts/'+d.id+'/run',{});d.job_id=job.id;d.state='submitted';node.querySelector('.ai-tool-state').textContent=label+' · 已提交';button.remove();monitorSimulation(node,job.id);}catch(error){node.append(element('div','ai-message-error',error.message));button.disabled=false;}});
    }else if(card.name==='engineering_calculator')node.append(element('div','',result.expression+' = '+result.value));
    else {
      if(card.name==='inspect_project_model')node.append(element('div','',result.model+' · '+result.block_count+' 个模块 · 未运行'));
      const details=element('details');details.append(element('summary','','查看工具返回'));details.append(element('pre','',JSON.stringify(result,null,2)));node.append(details);
    }
    return node;
  }
  function modelBuildCard(d){
    const node=element('section','ai-tool-card ai-build-card'),plan=d.plan;
    node.append(element('h4','',d.label),element('div','ai-build-status',plan.description));
    const chips=element('div','ai-build-summary');[plan.blocks.length+' 个模块',plan.connections.length+' 条连接','新建独立模型'].forEach(text=>chips.append(element('span','',text)));node.append(chips);
    const details=element('details'),summary=element('summary','','查看模块、参数与连线方案');details.append(summary,element('pre','',JSON.stringify(plan,null,2)));node.append(details);
    const note=element('p','ai-help','创建后检查模块连接；模型适用性仍需结合实际工况验证。'),state=element('div','ai-build-status'),progress=element('progress'),actions=element('div','ai-build-actions'),button=element('button','btn btn-primary','创建并打开模型');
    progress.max=100;progress.hidden=true;button.type='button';actions.append(button);node.append(note,state,progress,actions);
    function finished(file){
      d.file=file;progress.hidden=true;button.hidden=true;state.textContent='模型已创建，连接检查通过。';
      const link=element('a','btn btn-primary','下载 Simulink 模型');link.href=file.url;link.download=file.name;link.setAttribute('aria-label','下载文件 '+file.name);actions.replaceChildren(link);
    }
    async function monitor(id){
      if(!node.isConnected)return;
      try{
        const job=await api('simulations/'+id);state.textContent=job.message;progress.hidden=false;if(job.progress!=null)progress.value=job.progress;
        if(job.state==='built'&&job.file){finished(job.file);return;}
        if(['failed','stopped'].includes(job.state)){progress.hidden=true;state.classList.add('ai-message-error');button.hidden=true;return;}
        setTimeout(()=>monitor(id),1400);
      }catch(error){progress.hidden=true;state.textContent=error.message;button.textContent='重新获取建模结果';button.disabled=false;}
    }
    button.addEventListener('click',async()=>{button.disabled=true;state.textContent='正在提交搭建方案…';try{const job=await api('drafts/'+d.id+'/run',{});d.job_id=job.id;d.state='submitted';monitor(job.id);}catch(error){state.textContent=error.message;button.disabled=false;}});
    if(d.file)finished(d.file);else if(d.job_id){button.disabled=true;setTimeout(()=>monitor(d.job_id),0);}
    return node;
  }
  function softwareOpenCard(d){
    const node=element('div','ai-software-action'),button=element('button','btn',scope==='general'?'打开 Simulink':scope==='capacitance'?'打开辨识工作区':'打开 '+d.label+'模型'),error=element('span','ai-message-error');
    button.type='button';error.setAttribute('role','status');node.append(button,error);
    async function monitor(id){
      if(!node.isConnected)return;
      try{
        const job=await api('simulations/'+id);
        if(job.state==='opened'){button.disabled=false;return;}
        if(['failed','stopped'].includes(job.state)){error.textContent=job.message||'打开未完成，请重新发送请求。';button.disabled=false;return;}
        setTimeout(()=>monitor(id),1200);
      }catch{error.textContent='打开请求已过期，请重新发送请求。';}
    }
    if(d.job_id){button.disabled=true;setTimeout(()=>monitor(d.job_id),0);}
    button.addEventListener('click',async()=>{button.disabled=true;error.textContent='';try{const job=await api('drafts/'+d.id+'/run',{});d.job_id=job.id;d.state='submitted';monitor(job.id);}catch(failure){error.textContent=failure.message;button.disabled=false;}});
    return node;
  }
  async function monitorSimulation(node,id){
    const stateText=element('div','ai-tool-state','正在获取仿真状态…'),progress=element('progress'),image=element('img');
    progress.max=100;image.hidden=true;image.alt='Simulink 仿真模型';node.append(stateText,progress,image);
    let imageReady=false;
    async function update(){
      if(!node.isConnected)return;
      try{
        const job=await api('simulations/'+id);stateText.textContent=job.message;
        if(job.progress!=null)progress.value=job.progress;
        if(job.diagram_ready&&!imageReady){image.src='/api/project/simulation/'+id+'/diagram';image.hidden=false;imageReady=true;}
        if(['completed','stopped','failed','opened'].includes(job.state)){
          if(job.operation==='open_model')progress.hidden=true;
          if(job.state==='completed'){
            const analyze=element('button','btn','分析本次仿真结果');analyze.type='button';node.append(analyze);
            analyze.addEventListener('click',()=>{$('tools').checked=true;$('message').value='请读取并分析本会话仿真任务 '+id+' 的真实结果，说明统计口径与限制。';$('message').focus();feedback('已填写问题，点击发送后将读取并分析本次仿真。');});
          }
          return;
        }
        setTimeout(update,1200);
      }catch(error){stateText.textContent=error.message;}
    }
    update();
  }
  function modelOptions(){
    const select=$('model');select.replaceChildren();models=status.models||[];
    models.forEach(model=>select.add(new Option(model.name,model.id)));
    select.add(new Option('自定义模型 ID / 接入点 ID','__custom__'));
    const remembered=status.selected||status.preferred_model;
    if(remembered){if(models.some(m=>m.id===remembered.id))select.value=remembered.id;else {select.value='__custom__';$('custom-id').value=remembered.id;['vision','thinking','tools'].forEach(name=>{$('custom-'+name).checked=Boolean(remembered[name]);});}}
    const catalogue=['doubao','qwen'].includes(status.provider);
    $('model-note').textContent=catalogue?'列表为官方模型目录，实际可用性取决于账户权限与地域。验证会发送简短测试请求，按服务商规则计费。':'列表实时读取官方接口，仅展示当前平台返回的模型。可刷新列表或填写正式模型 ID；自定义 DeepSeek 模型验证会发送简短测试请求。';
    updateModelCapabilities();
  }
  function updateModelCapabilities(){
    const custom=$('model').value==='__custom__';$('custom-model').hidden=!custom;
    const model=models.find(m=>m.id===$('model').value);
    $('model-capabilities').textContent=custom?'请填写真实模型 ID 及其能力。':model?['文本',model.vision?'图片理解':'不支持图片',model.thinking_required?'始终开启思考':model.thinking?'可选深度思考':'标准回答',model.tools?'工具调用':'无工具调用'].join(' · '):'';
  }
  function showGate(step=1){
    $('access').hidden=false;$('chat-shell').hidden=true;
    $('key-form').hidden=step!==1;$('model-form').hidden=step!==2;
    $('access-step').textContent=step===1?'01 / 连接服务商':'02 / 选择模型';
    $('access-title').textContent=step===1?'使用你的 API Key':'选择适合当前问题的版本';
    $('disconnect').hidden=!status?.connected;$('settings').hidden=!status?.ready;
    if(step===1&&status?.connected){
      $('provider').value=status.provider;
      if(status.provider==='qwen'){$('qwen-region').value=status.connection?.region||'cn-beijing';$('qwen-workspace').value=status.connection?.workspace||'';}
      if(status.provider==='kimi')$('kimi-region').value=status.connection?.region||'cn';
      connectionFields();
    }
    if(step===2)modelOptions();
  }
  function configureReady(){
    ready=true;$('access').hidden=true;$('chat-shell').hidden=false;$('settings').hidden=false;$('disconnect').hidden=false;
    $('connection-state').textContent=(status.provider_name||providerNames[status.provider])+' · '+status.selected.name;
    $('thinking').disabled=!status.selected.thinking||Boolean(status.selected.thinking_required);
    $('thinking').checked=Boolean(status.selected.thinking);
    $('thinking').parentElement.title=status.selected.thinking_required?'当前模型始终开启思考。':'显示服务商实际返回的推理内容或摘要。';
    $('tools').disabled=!status.selected.tools;if($('tools').disabled)$('tools').checked=false;
    $('suggestions').replaceChildren(...suggestions[scope].map(text=>{const button=element('button','',text);button.type='button';button.addEventListener('click',()=>{$('message').value=text;$('message').focus();});return button;}));
  }
  async function refreshHistory(){
    status=await api('status?scope='+scope);const select=$('history');
    select.replaceChildren();(status.threads||[]).forEach(item=>select.add(new Option(item.title,item.id)));
    if(thread)select.value=thread.id;
  }
  async function loadThread(id){
    thread=await api('threads/'+id);renderEmpty();
    savedState(thread);
    thread.messages.forEach(message=>appendMessage(message));
    if(!thread.messages.length)renderEmpty();
    autoFollow=true;endScroll();
    if(thread.job_id){
      const current=thread.messages.filter(m=>m.role==='assistant').at(-1);
      if(current){current.content='';current.reasoning='';current.tools=[];current.error='';const view=rendered.get(current.id);view.tools.replaceChildren();view.renderedTools=0;renderAssistant(view);setBusy(true);jobId=thread.job_id;listen(jobId,view);}
    }
  }
  async function newThread(){if(busy)return;for(const item of pending)await api('attachments/'+item.id,{},'DELETE').catch(()=>{});pending=[];renderPending();thread=await api('threads',{scope});renderEmpty();savedState(thread);await refreshHistory();historyChannel?.postMessage({scope});}
  async function legacyNotice(){
    const archives=await chatUpgradeArchive('list');const matching=archives.filter(a=>a.provider===status.provider&&a.model.id===status.selected.id);
    $('legacy-notice').hidden=!matching.length;$('legacy-text').textContent='检测到 '+matching.length+' 个更新前对话，可恢复到当前 API Key 的本机历史。';return matching;
  }
  async function initialize(){
    try{
      status=await api('status?scope='+scope);
      if(status.ready){configureReady();await refreshHistory();if(status.active_thread)await loadThread(status.active_thread);else if(status.threads.length)await loadThread(status.threads[0].id);else await newThread();await legacyNotice().catch(()=>{});}
      else if(status.connected)showGate(2);else {showGate(1);const backups=await chatUpgradeArchive('list').catch(()=>[]);if(backups.length)feedback('检测到 '+backups.length+' 个更新前对话备份，连接原服务商和模型后可恢复。',false,true);}
    }catch(error){showGate(1);feedback(error.message,true,true);}
  }
  const providerNames={deepseek:'DeepSeek',doubao:'豆包',kimi:'Kimi',qwen:'千问'};
  function connectionFields(){
    const provider=$('provider').value;
    $('kimi-region-field').hidden=provider!=='kimi';$('qwen-settings').hidden=provider!=='qwen';
    const needsWorkspace=['ap-northeast-1','eu-central-1'].includes($('qwen-region').value);
    $('qwen-workspace').required=provider==='qwen'&&needsWorkspace;
    $('key-help').href={deepseek:'https://platform.deepseek.com/api_keys',doubao:'https://console.volcengine.com/ark/region:cn-beijing/apiKey',kimi:$('kimi-region').value==='intl'?'https://platform.kimi.ai/console/api-keys':'https://platform.moonshot.cn/console/api-keys',qwen:'https://help.aliyun.com/zh/model-studio/get-api-key'}[provider];
  }
  $('provider').addEventListener('change',()=>{$('key').value='';connectionFields();});
  $('qwen-region').addEventListener('change',connectionFields);$('kimi-region').addEventListener('change',()=>{$('key').value='';connectionFields();});
  connectionFields();
  $('key-visibility').addEventListener('click',()=>{const visible=$('key').type==='password';$('key').type=visible?'text':'password';$('key-visibility').textContent=visible?'隐藏':'显示';$('key-visibility').setAttribute('aria-label',visible?'隐藏密钥':'显示密钥');});
  $('key-form').addEventListener('submit',async event=>{
    event.preventDefault();$('connect').disabled=true;feedback('正在连接服务商…',false,true);
    const provider=$('provider').value;const connection=provider==='qwen'?{region:$('qwen-region').value,workspace:$('qwen-workspace').value.trim()}:provider==='kimi'?{region:$('kimi-region').value}:{};
    try{status=await api('connect',{provider,key:$('key').value.trim(),connection});$('key').value='';ready=false;showGate(2);historyChannel?.postMessage({connection:true});feedback('请选择模型版本。',false,true);}catch(error){feedback(error.message,true,true);}finally{$('connect').disabled=false;}
  });
  $('model').addEventListener('change',updateModelCapabilities);
  $('refresh-models').addEventListener('click',async()=>{const button=$('refresh-models');button.disabled=true;try{status=await api('models/refresh',{});modelOptions();feedback('模型列表已更新。',false,true);}catch(error){feedback(error.message,true,true);}finally{button.disabled=false;}});
  $('change-provider').addEventListener('click',()=>{showGate(1);$('key').value='';});
  $('model-form').addEventListener('submit',async event=>{
    event.preventDefault();$('select-model').disabled=true;feedback('正在验证模型访问权限…',false,true);
    let model=$('model').value;const capabilities={vision:$('custom-vision').checked,thinking:$('custom-thinking').checked,tools:$('custom-tools').checked};if(model==='__custom__')model=$('custom-id').value.trim();
    try{status=await api('select',{model,capabilities});configureReady();pending=[];renderPending();await initialize();historyChannel?.postMessage({connection:true});feedback('');}catch(error){feedback(error.message,true,true);}finally{$('select-model').disabled=false;}
  });
  $('settings').addEventListener('click',()=>{if(!busy)showGate(2);});
  $('disconnect').addEventListener('click',async()=>{try{await api('disconnect',{});streamAbort?.abort();jobId=null;setBusy(false);ready=false;thread=null;pending=[];rendered.clear();status=null;$('key').value='';$('connection-state').textContent='连接已断开';showGate(1);historyChannel?.postMessage({connection:true});feedback('已断开连接，历史记录仍保存在本机。',false,true);}catch(error){feedback(error.message,true);}});
  $('restore-legacy').addEventListener('click',async()=>{
    const button=$('restore-legacy');button.disabled=true;
    try{const archives=await legacyNotice();let current=null;
      for(const archive of archives){const files=[];for(const file of archive.files||[]){const bytes=new Uint8Array(file.raw);let binary='';for(let n=0;n<bytes.length;n+=8192)binary+=String.fromCharCode(...bytes.subarray(n,n+8192));files.push({id:file.id,name:file.name,base64:btoa(binary)});}
        const restored=await api('import-legacy',{...archive,files});await chatUpgradeArchive('delete',archive.id);if(restored.scope===scope)current=restored.thread_id;}
      await refreshHistory();if(current)await loadThread(current);await legacyNotice();feedback('更新前记录已恢复并保存到本机。');historyChannel?.postMessage({scope});
    }catch(error){feedback(error.message,true);}finally{button.disabled=false;}
  });
  $('new-thread').addEventListener('click',()=>newThread().catch(error=>feedback(error.message,true)));
  $('history').addEventListener('change',()=>loadThread($('history').value).catch(error=>feedback(error.message,true)));
  $('clear').addEventListener('click',async()=>{if(!thread||busy)return;try{await api('threads/'+thread.id,{},'DELETE');await newThread();}catch(error){feedback(error.message,true);}});
  $('export').addEventListener('click',async()=>{
    if(!thread)return;
    try{const current=await api('threads/'+thread.id);let text='# '+titles[scope]+'\n\n模型：'+status.selected.name+'\n\n';
      current.messages.forEach(m=>{text+='## '+(m.role==='user'?'用户':'助手')+'\n\n'+m.content+'\n\n';if(m.reasoning)text+='<details><summary>服务商返回的推理内容 / 摘要</summary>\n\n'+m.reasoning+'\n\n</details>\n\n';if(m.attachments?.length)text+='附件：'+m.attachments.map(a=>a.name).join('、')+'\n\n';});
      const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'})),link=element('a');link.href=url;link.download=titles[scope]+'-对话.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){feedback(error.message,true);}
  });
  async function uploadFiles(files){
    if(!ready||busy){feedback('请先连接模型，并结束当前回复后添加附件。',true);return;}
    for(const file of files){
      if(pending.length>=6){feedback('每轮最多6个附件。',true);break;}
      if(file.size>8*1024**2){feedback(file.name+' 超过8 MB，请拆分后上传。',true);continue;}
      if((file.type.startsWith('image/')||/\.(png|jpe?g|webp|gif)$/i.test(file.name))&&!status.selected.vision){feedback('所选模型不支持图片，请在模型设置中更换视觉模型。',true);continue;}
      uploading++;$('add-file').disabled=true;renderPending();feedback('正在解析附件：'+file.name);
      try{const form=new FormData();form.append('file',file);const response=await fetch('/api/project/ai/upload',{method:'POST',body:form});const item=await response.json();if(!response.ok)throw new Error(item.error||'附件上传失败。');pending.push(item);feedback(item.note||'附件已添加，点击发送后会交给当前服务商。');}
      catch(error){feedback(error.message,true);}finally{uploading--;renderPending();$('add-file').disabled=busy||uploading>0;}
    }
  }
  $('add-file').addEventListener('click',()=>$('file-input').click());
  $('file-input').addEventListener('change',event=>{uploadFiles([...event.target.files]);event.target.value='';});
  document.addEventListener('paste',event=>{
    if(!ready||$('chat-shell').hidden||root.closest('details')?.open===false)return;
    const target=event.target;if(target instanceof HTMLInputElement||target instanceof HTMLTextAreaElement&&target!==$('message'))return;
    const files=[...(event.clipboardData?.files||[])];
    if(files.length){event.preventDefault();uploadFiles(files);}
    else if(target===$('message')&&/^(?:[A-Za-z]:\\|file:\/\/)/.test((event.clipboardData?.getData('text/plain')||'').trim())){
      event.preventDefault();feedback('浏览器未提供复制文件的内容，请拖拽文件或点击添加附件。',true);
    }
  });
  root.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();root.classList.add('is-dragging');}});
  root.addEventListener('dragleave',event=>{if(!root.contains(event.relatedTarget))root.classList.remove('is-dragging');});
  root.addEventListener('drop',event=>{if(event.dataTransfer?.files.length){event.preventDefault();root.classList.remove('is-dragging');uploadFiles([...event.dataTransfer.files]);}});
  $('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();send(false);}});
  $('composer').addEventListener('submit',event=>{event.preventDefault();send(false);});
  async function send(retry=false){
    if(!ready||busy||uploading)return;
    const text=$('message').value.trim();if(!retry&&!text&&!pending.length)return;
    if(!thread){try{await newThread();}catch(error){feedback(error.message,true);return;}}
    setBusy(true);autoFollow=true;feedback('');
    try{
      const result=await api('messages',{thread_id:thread.id,text,attachments:pending.map(a=>a.id),thinking:$('thinking').checked,allow_tools:$('tools').checked,share_data:$('share-data').checked,retry});
      if(retry){const updated=await api('threads/'+thread.id);renderEmpty();updated.messages.forEach(m=>{if(m.id!==result.assistant_id)appendMessage(m);});}
      else{appendMessage({id:'local-'+Date.now(),role:'user',content:text,attachments:pending,shared_data:$('share-data').checked});$('message').value='';pending=[];renderPending();}
      $('storage-state').textContent='正在生成并自动保存…';
      const message={id:result.assistant_id,role:'assistant',model:status.selected.name,content:'',reasoning:'',tools:[],state:'responding'};
      const view=appendMessage(message);jobId=result.job_id;await listen(jobId,view);
    }catch(error){feedback(error.message,true);setBusy(false);}
  }
  async function listen(id,view){
    streamAbort=new AbortController();
    try{
      const response=await fetch('/api/project/ai/jobs/'+id+'/events',{signal:streamAbort.signal,cache:'no-store'});
      if(!response.ok){const value=await response.json();throw new Error(value.error||'无法读取流式响应。');}
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',finished=false;
      while(true){
        const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});
        let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){const raw=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);const data=raw.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(!data)continue;const event=JSON.parse(data),m=view.message;
          if(event.event==='reasoning'){m.reasoning+=event.text;if(!view.reasoning.open&&!m.content)view.reasoning.open=true;scheduleRender(view);}
          else if(event.event==='answer'){m.content+=event.text;scheduleRender(view);}
          else if(event.event==='tool_start'){m.notice='正在调用：'+(toolNames[event.name]||event.name);scheduleRender(view);}
          else if(event.event==='tool_result'){m.tools.push({name:event.name,result:event.result});m.notice='';renderAssistant(view);}
          else if(event.event==='notice'){m.notice=event.text;renderAssistant(view);}
          else if(event.event==='error'){m.error=event.text;renderAssistant(view);}
          else if(event.event==='finish'){m.state=event.state;m.usage=event.usage;savedState(event);if(m.state==='stopped')m.notice='已停止生成。';if(m.content)view.reasoning.open=false;renderAssistant(view);finished=true;historyChannel?.postMessage({scope});}
        }
      }
      if(!finished)throw new Error('回复连接已断开，可刷新恢复会话或重新生成。');
    }catch(error){if(error.name!=='AbortError'){view.message.error=error.message;renderAssistant(view);}}
    finally{if(jobId===id){jobId=null;setBusy(false);if(view.message.state==='stopped')feedback('已停止生成。');await refreshHistory().catch(()=>{});}streamAbort=null;}
  }
  $('stop').addEventListener('click',async()=>{if(!jobId)return;$('stop').disabled=true;feedback('正在停止生成…');try{await api('jobs/'+jobId+'/stop',{});}catch(error){feedback(error.message,true);$('stop').disabled=false;}});
  let syncing=false,pendingConnectionSync=false;
  async function syncHistory(connectionChanged=false){
    if(syncing){pendingConnectionSync=pendingConnectionSync||connectionChanged;return;}
    if((!ready&&!connectionChanged)||busy||document.hidden)return;
    syncing=true;
    try{
      const previous=status,identity=value=>JSON.stringify([value?.connection_id,value?.provider,value?.selected?.id,value?.connection]);
      const fresh=await api('status?scope='+scope);
      if(!fresh.ready){status=fresh;ready=false;thread=null;pending=[];renderPending();rendered.clear();showGate(fresh.connected?2:1);$('connection-state').textContent=fresh.connected?'请完成模型选择':'连接模型，开始交流';return;}
      const changed=!ready||identity(previous)!==identity(fresh);
      status=fresh;
      if(changed){
        ready=false;thread=null;pending=[];$('message').value='';renderPending();configureReady();await refreshHistory();
        const id=status.active_thread||status.threads[0]?.id;
        if(id)await loadThread(id);else await newThread();
        await legacyNotice().catch(()=>{});feedback('模型连接已同步。');return;
      }
      const id=thread?.id,updated=thread?.updated_at;await refreshHistory();
      const item=status.threads.find(t=>t.id===id);
      if(item&&item.updated_at!==updated)await loadThread(id);
      else if(!item){const next=status.active_thread||status.threads[0]?.id;if(next)await loadThread(next);else await newThread();}
    }catch(error){feedback(error.message,true);}finally{syncing=false;if(pendingConnectionSync){pendingConnectionSync=false;queueMicrotask(()=>syncHistory(true));}}
  }
  historyChannel?.addEventListener('message',event=>{if(event.data.connection||event.data.scope===scope)syncHistory(Boolean(event.data.connection));});
  window.addEventListener('focus',()=>syncHistory(true));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)syncHistory(true);});
  root.closest('details')?.addEventListener('toggle',()=>{if(root.closest('details').open){syncHistory();endScroll();}});
  initialize();
});
