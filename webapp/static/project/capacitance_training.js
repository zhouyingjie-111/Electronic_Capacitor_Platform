/** CNN 工作台：数据选择、预处理、训练与编译共用任务接口；只渲染已返回的结果，错误反馈保留供重试。 */
document.addEventListener('DOMContentLoaded',async()=>{
  const form=document.getElementById('cap-training-form');if(!form)return;
  const $=name=>document.getElementById('cap-'+name),base='/api/project/cap-training/';
  const active=new Set(['queued','preparing','training','compiling']);
  let dataset=null,model=null,busy=false,uploading=false,activeJob=null,activeKind=null,prepared=null,saved=null,previewVersion=0,timer=null;
  const cursors=new Map(),logIds={run:null,compile:null};
  function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
  function feedback(text,error=false,compile=false){const target=$(compile?'compile-feedback':'feedback');target.textContent=text;target.classList.toggle('error',error);}
  async function api(path,body,method){
    const options=body===undefined?{cache:'no-store'}:{method:method||'POST',headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body instanceof FormData?body:JSON.stringify(body)};
    const response=await fetch(base+path,options);let data;try{data=await response.json();}catch{throw new Error('辨识服务暂不可用。');}
    if(!response.ok)throw new Error(data.error||'操作未完成。');return data;
  }
  function controls(){
    $('dataset-file-field').hidden=$('dataset-source').value!=='upload';$('model-file-field').hidden=$('model-source').value!=='upload';
    ['dataset-source','dataset-file','model-source','model-file'].forEach(k=>{$(k).disabled=busy||uploading;});
    const missing=$('dataset-source').value==='upload'&&!dataset;
    $('start').disabled=$('prepare').disabled=busy||uploading||missing;
    $('compile').disabled=busy||uploading||($('model-source').value==='upload'?!model:!saved);
    $('stop').hidden=!busy;$('stop').disabled=!activeJob;
  }
  function remember(){try{sessionStorage.setItem('cap-data-selection',JSON.stringify({source:$('dataset-source').value,token:dataset}));}catch{}}
  function rows(target,values){target.replaceChildren(...values.map(values=>{const tr=node('tr');values.forEach(v=>tr.append(node('td',String(v??'—'))));return tr;}));}
  function showPreview(data){
    rows($('files'),data.files.map(f=>[f.name,f.group,f.channel,f.view,f.dt_us,f.declared_samples,f.use]));
    rows($('labels'),Object.entries(data.labels).map(([g,v])=>[g,...v]));
    $('preview-file').replaceChildren(...data.files.map(f=>new Option(f.name,f.name)));$('preview-file').value=data.selected;
    const tr=node('tr');data.columns.forEach(c=>tr.append(node('th',c)));$('sample-head').replaceChildren(tr);rows($('samples'),data.rows);
  }
  async function loadPreview(){
    const version=++previewVersion;
    if($('dataset-source').value==='upload'&&!dataset){rows($('files'),[]);rows($('labels'),[]);rows($('samples'),[]);$('preview-file').replaceChildren();feedback('请选择示波器数据 ZIP。');controls();return;}
    try{const data=await api('preview?dataset='+encodeURIComponent($('dataset-source').value==='default'?'default':dataset));if(version===previewVersion){showPreview(data);feedback('');}}
    catch(error){if(version===previewVersion){feedback(error.message,true);if($('dataset-source').value==='upload'){dataset=null;remember();controls();}}}
  }
  $('dataset-source').addEventListener('change',()=>{remember();controls();loadPreview();});
  $('preview-file').addEventListener('change',async()=>{
    const version=++previewVersion;
    try{const data=await api('preview?dataset='+encodeURIComponent($('dataset-source').value==='default'?'default':dataset)+'&file='+encodeURIComponent($('preview-file').value));if(version===previewVersion)showPreview(data);}
    catch(error){feedback(error.message,true);}
  });
  $('model-source').addEventListener('change',()=>{feedback('',false,true);controls();});
  for(const kind of ['dataset','model'])$(kind+'-file').addEventListener('change',async()=>{
    const file=$(kind+'-file').files[0];if(!file)return;
    if(kind==='dataset'){dataset=null;remember();await loadPreview();}else model=null;
    uploading=true;controls();feedback('正在上传并检查文件…',false,kind==='model');
    try{const body=new FormData();body.append('file',file);const result=await api('upload/'+kind,body);
      if(kind==='dataset'){dataset=result.token;remember();showPreview(result.preview);}else model=result.token;
      feedback('已选择：'+result.name,false,kind==='model');
    }catch(error){feedback(error.message,true,kind==='model');}finally{uploading=false;controls();}
  });
  function figure(url,label){const f=node('figure'),a=node('a'),img=node('img');a.href=url;a.dataset.imageViewer='';img.src=url;img.alt=label;img.loading='lazy';a.append(img);f.append(a,node('figcaption',label));return f;}
  function showFeatures(job){
    if(!job?.groups?.length)return;prepared=job;$('prepared').hidden=false;
    $('prepare-download').href=base+job.id+'/download';$('prepare-download').download='capacitance_preprocessing.zip';
    $('splits').replaceChildren();for(const name of ['train','validation','test']){
      const count=Object.values(job.splits||{}).reduce((n,g)=>n+(g[name]?.kept||0),0),span=node('span');span.append(node('b',String(count)),document.createTextNode(' '+({train:'训练窗口',validation:'验证窗口',test:'测试窗口'}[name])));$('splits').append(span);
    }
    $('image-group').replaceChildren(...job.groups.map(g=>new Option(g,g)));featureImages();
  }
  function featureImages(){if(!prepared)return;const group=$('image-group').value;$('feature-images').replaceChildren(...['CH1','CH3','CH4'].map((ch,i)=>figure(base+prepared.id+'/artifact/'+group+'_'+ch+'.png',ch+' · '+['阵列端电压','阵列总电流','负载电流'][i])));}
  $('image-group').addEventListener('change',featureImages);
  const format=(v,d=5)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
  function showModel(job){
    if(!job?.metrics)return;saved=job;const constants=new Set(job.metrics.constant_outputs||[]);
    rows($('metrics'),job.metrics.outputs.map(name=>{const m=job.metrics.test[name];return [name,name.startsWith('ESR')?'Ω':'μF',format(m.mre_percent,3),format(m.rmse),format(m.r2),constants.has(name)?'固定参考值':'回归输出'];}));
    $('model-download').hidden=false;$('model-download').href=base+job.id+'/download';$('model-download').download='capacitance_model.zip';
    resultImages();$('loss-figure').replaceChildren(figure(base+job.id+'/chart/loss.svg','训练与验证损失'));showFeatures(job);controls();
  }
  function resultImages(){if(!saved)return;const target=$('result-target').value;
    $('result-figures').replaceChildren(figure(base+saved.id+'/chart/scatter.svg?target='+encodeURIComponent(target),target+' · 测试窗口散点图'),figure(base+saved.id+'/chart/prediction.svg?target='+encodeURIComponent(target),target+' · 逐窗口预测对比'));
  }
  $('result-target').addEventListener('change',resultImages);
  function appendLog(job){
    const kind=job.kind==='compile'?'compile':'run',log=$(kind==='compile'?'compile-log':'log');
    if(logIds[kind]!==job.id){logIds[kind]=job.id;log.textContent='';cursors.set(job.id,0);}
    const follow=log.scrollHeight-log.scrollTop-log.clientHeight<50;
    if(job.logs?.length)log.textContent+=(log.textContent?'\n':'')+job.logs.join('\n');
    cursors.set(job.id,job.next_cursor);if(follow)log.scrollTop=log.scrollHeight;
    $(kind==='compile'?'compile-stage':'stage').textContent=job.message;
  }
  async function drain(job){appendLog(job);while(job.has_more){job=await api(job.id+'?after='+cursors.get(job.id));appendLog(job);}return job;}
  function complete(job){
    if(job.state==='completed'){
      if(job.kind==='training')showModel(job);
      if(job.kind==='prepare')showFeatures(job);
      if(job.kind==='compile'){$('compile-download').href=base+job.id+'/download';$('compile-download').download='capacitance_compilation.zip';$('compile-download').hidden=false;}
    }
  }
  async function poll(){
    if(!activeJob)return;
    try{const job=await drain(await api(activeJob+'?after='+(cursors.get(activeJob)||0)));busy=active.has(job.state);controls();if(busy){timer=setTimeout(poll,1500);}else{complete(job);activeJob=null;controls();}}
    catch(error){feedback(error.message,true,activeKind==='compile');timer=setTimeout(poll,3000);}
  }
  async function start(kind){
    if(busy||uploading)return;busy=true;activeKind=kind;controls();feedback('',false,kind==='compile');
    try{const job=await api('start',{kind,dataset:$('dataset-source').value==='default'?'default':dataset,model:$('model-source').value==='latest'?'latest':model});activeJob=job.id;await drain(job);controls();poll();}
    catch(error){busy=false;activeJob=null;controls();feedback(error.message,true,kind==='compile');}
  }
  form.addEventListener('submit',e=>{e.preventDefault();start('training');});$('prepare').addEventListener('click',()=>start('prepare'));$('compile').addEventListener('click',()=>start('compile'));
  $('stop').addEventListener('click',async()=>{if(activeJob){$('stop').disabled=true;try{await api(activeJob+'/stop',{});}catch(error){feedback(error.message,true);}}});
  try{const previous=JSON.parse(sessionStorage.getItem('cap-data-selection')||'null');if(previous?.source==='upload'){$('dataset-source').value='upload';dataset=previous.token;}}catch{}
  controls();await loadPreview();
  try{const state=await api('current');if(state.saved)showModel(state.saved);
    for(const kind of ['prepare','training','compile'])if(state[kind]){await drain(state[kind]);complete(state[kind]);if(active.has(state[kind].state)){activeJob=state[kind].id;activeKind=kind;busy=true;}}
    controls();if(activeJob)poll();
  }catch(error){feedback(error.message,true);}
  window.addEventListener('pagehide',()=>clearTimeout(timer));
});
