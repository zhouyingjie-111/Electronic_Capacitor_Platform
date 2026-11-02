/** 串口页面渲染：连接状态来自同一个后端服务；仅按所属功能选择帧，页面切换不主动连接/断开设备。 */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.querySelector('[data-serial-module]');
  if (!root) return;
  const byId = id => document.getElementById(id);
  const kind = root.dataset.serialModule === 'tracking' ? 'TRK' : 'CAP';
  const port = byId('serial-port'), baud = byId('serial-baud');
  const connect = byId('serial-connect'), feedback = byId('serial-feedback');
  const status = byId('serial-status'), dataStatus = byId('serial-data-status');
  const log = byId('serial-log'), chart = byId('serial-chart');
  let connected = false, busy = false, cursor = 0, connectionId = null;
  let latest = null, samples = [], lines = [], lastFrameId = null, stopped = false;
  let seen = {}, capData = {}, capValid = false, hasCapMonitor = false;
  const storageKey = 'serial-chart-mode-' + kind;
  const colors = ['#3578c8', '#c08745', '#8570aa', '#509ca7'];
  async function api(path, data) {
    const response = await fetch('/api/project/serial/' + path, data === undefined ?
      {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '通信请求失败。');
    return result;
  }
  function message(text, error=false) {
    feedback.textContent = text;
    feedback.classList.toggle('is-error', error);
  }
  function format(value, digits=3) {
    return value === null || value === undefined || !Number.isFinite(value) ? '—' :
      Number(value).toFixed(digits).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1');
  }
  function resetData() {
    latest = null; samples = []; lastFrameId = null; seen={}; capData={}; capValid=false; hasCapMonitor=false;
    root.querySelectorAll('[data-serial-value]').forEach(node => {node.textContent = '—';});
    if (byId('serial-direction')) byId('serial-direction').textContent = '—';
    if (byId('serial-control-state')) byId('serial-control-state').textContent = '—';
    dataStatus.textContent = '等待设备数据';
    if(byId('serial-cap-state'))byId('serial-cap-state').textContent='等待辨识';
    if(byId('serial-cap-fault'))byId('serial-cap-fault').textContent='';
    draw();
  }
  function renderConnection(state) {
    connected = state.connected;
    status.classList.toggle('is-connected', connected);
    status.querySelector('b').textContent = connected ? '已连接 · ' + state.port : '未连接';
    connect.textContent = connected ? '断开连接' : '连接串口';
    port.disabled = baud.disabled = connected || busy;
    connect.disabled = busy;
    if(state.upload_rate) byId('serial-rate').value = String(state.upload_rate);
    if(state.feedback) message(state.feedback.text, state.feedback.error);
    byId('serial-refresh').disabled = connected || busy;
    root.querySelectorAll('[data-serial-command], #serial-rate').forEach(node => {
      node.disabled = !connected || busy;
    });
    if (connected) {
      if (![...port.options].some(option => option.value === state.port)) port.add(new Option(state.port, state.port));
      port.value = state.port; baud.value = String(state.baud);
    }
  }
  function renderFrame(frame) {
    if (kind === 'CAP') {
      if(frame.type==='TRK' && hasCapMonitor)return;
      // Legacy TRK.vg1 carries the CCS Vc1 field; CAPMON.vc1 is the current source.
      // Never reuse the TRK ANN timing as a CNN timing.
      if(frame.type==='TRK') frame={...frame,data:{vc1:frame.data.vg1,ic:frame.data.ic,io:frame.data.io,isr_us:frame.data.isr_us}};
      if(frame.type==='CAPMON')hasCapMonitor=true;
      Object.assign(capData,frame.data);
      if(frame.type==='CAP')capValid=true;
      if(frame.data.valid===0)capValid=false;
      latest=frame;
      const states=['等待辨识','采集中','小波处理','生成特征','CNN 推理','辨识完成','辨识失败'];
      byId('serial-cap-state').textContent=states[capData.state] || (capValid?'辨识完成':'等待辨识');
      const faults={1:'ADC 中断溢出',2:'采样超出量程',4:'采样时序异常',8:'模型计算异常',16:'采样配置无效',32:'辨识已取消',64:'设备尚未就绪'};
      byId('serial-cap-fault').textContent=Object.entries(faults).filter(([bit])=>(capData.fault||0)&Number(bit)).map(([,text])=>text).join(' · ');
      if(frame.type==='CAPMON' || frame.type==='TRK')samples.push(frame);
      if(samples.length>300)samples.shift();
    } else {latest=frame;samples.push(frame);if(samples.length>300)samples.shift();}
    root.querySelectorAll('[data-serial-value]').forEach(node => {
      const key=node.dataset.serialValue;
      const value = kind==='CAP' ? (/^(c|esr)[1-4]$/.test(key) && !capValid ? null:capData[key]) : frame.data[key];
      node.textContent = format(value, Number(node.dataset.digits || 3)) + (value == null ? '' : node.dataset.unit || '');
    });
    if (kind === 'TRK') {
      byId('serial-direction').textContent = frame.data.dt_given == null ? '—' : frame.data.direction_state === 0 ? '上升沿' : '下降沿';
      byId('serial-control-state').textContent = frame.data.ann_active === 1 ? 'ANN 接管' : 'PI 控制';
    }
    draw();
  }
  function dataAge() {
    const stale = !connected || !latest || Date.now()/1000 - latest.received_at > 2;
    root.querySelector('.serial-monitor').classList.toggle('is-stale', stale);
    if (!latest) dataStatus.textContent = kind === 'CAP' ? '等待电容辨识数据' : '等待快速追踪数据';
    else dataStatus.textContent = (stale ? '数据待更新' : '正在接收') + ' · ' +
      new Date(latest.received_at*1000).toLocaleTimeString('zh-CN', {hour12:false});
  }
  function draw() {
    if (!chart || root.open === false) return;
    const width = chart.parentElement.clientWidth;
    const height = 230, ratio = window.devicePixelRatio || 1;
    chart.width = width*ratio; chart.height = height*ratio;
    chart.style.height = height+'px';
    const ctx = chart.getContext('2d');ctx.scale(ratio, ratio);
    ctx.fillStyle = '#fff';ctx.fillRect(0,0,width,height);
    const mode = byId('serial-chart-kind').value;
    const plotSamples=samples;
    if(width<150)return;
    const keys = kind==='CAP' ? ({current:['ic','io'],voltage:['vc1']}[mode] || ['vc1']) : mode === 'current' ? ['io','il1','il2'] : mode === 'voltage' ? ['vin','vc1','vg1'] : ['dt_given','pi_duty'];
    const labels = {ic:'电容电流 / A',io:'负载电流',il1:'电感电流 1',il2:'电感电流 2',vin:'母线电压',vc1:'VC1',vg1:'VG1',dt_given:'DTgiven',pi_duty:'PI 占空比'};
    const points = plotSamples.flatMap(frame => keys.map(key => frame.data[key])).filter(v => v != null && Number.isFinite(v));
    byId('serial-chart-empty').hidden=points.length>0;
    if(!points.length)return;
    let min = Math.min(...points), max = Math.max(...points);
    const pad = Math.max((max-min)*0.12, mode === 'duty' ? 0.01 : 1);
    min-=pad;max+=pad;
    const left = 60,right = width-18,columns=width<600?2:keys.length,top=width<600 && keys.length>2?58:38,bottom=height-34;
    ctx.font = '13px "Times New Roman", "SimSun", serif';
    ctx.lineWidth = 1;
    for (let n=0;n<5;n++) {
      const y=top+(bottom-top)*n/4;
      ctx.strokeStyle='#e8edf2';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
      ctx.fillStyle='#8190a2';ctx.textAlign='right';ctx.fillText(format(max-(max-min)*n/4,mode==='duty'?3:1),left-9,y+4);
    }
    const first=plotSamples[0].received_at,last=Math.max(plotSamples[plotSamples.length-1].received_at,first+0.1);
    keys.forEach((key,index) => {
      ctx.strokeStyle=colors[index];ctx.fillStyle=colors[index];ctx.lineWidth=1.6;ctx.beginPath();let started=false;
      plotSamples.forEach(frame => {
        const value=frame.data[key];
        if (value==null || !Number.isFinite(value)) {started=false;return;}
        const x=left+(frame.received_at-first)/(last-first)*(right-left),y=bottom-(value-min)/(max-min)*(bottom-top);
        if(started)ctx.lineTo(x,y);else ctx.moveTo(x,y);started=true;
      });ctx.stroke();
      if(plotSamples.length===1 && plotSamples[0].data[key]!=null){ctx.beginPath();ctx.arc(left,bottom-(plotSamples[0].data[key]-min)/(max-min)*(bottom-top),3,0,Math.PI*2);ctx.fill();}
      ctx.textAlign='left';ctx.fillText('● '+labels[key],left+(index%columns)*((right-left)/columns),20+Math.floor(index/columns)*20);
    });
    ctx.fillStyle='#8190a2';ctx.textAlign='left';ctx.fillText(new Date(first*1000).toLocaleTimeString('zh-CN',{hour12:false}),left,height-10);
    ctx.textAlign='right';ctx.fillText(new Date(last*1000).toLocaleTimeString('zh-CN',{hour12:false}),right,height-10);
  }
  function renderLog() {
    log.textContent = lines.length ? lines.join('\n') : '暂无通信记录';
    if(byId('serial-autoscroll').checked) log.scrollTop = log.scrollHeight;
  }
  async function refreshPorts() {
    try {
      const data = await api('ports'), previous = port.value || localStorage.getItem('serial-port') || '';
      port.replaceChildren(new Option(data.ports.length ? '请选择串口设备' : '未发现串口设备', ''));
      data.ports.forEach(p => port.add(new Option(p.device+' · '+p.description,p.device)));
      if(data.ports.some(p => p.device===previous))port.value=previous;
      else port.value=data.ports.find(p => p.recommended)?.device || '';
      if(!data.ports.length && !connected)message('未发现串口设备，请连接开发板后刷新。');
    } catch(error) {message(error.message,true);}
  }
  async function send(command) {
    try {
      await api('send',{command});message('指令已发送，等待设备应答。');
    } catch(error) {message(error.message,true);}
  }
  connect.addEventListener('click',async () => {
    if(busy)return;
    busy=true;connect.disabled=true;
    try {
      const state=await api(connected?'disconnect':'connect',connected?{}:{port:port.value,baud:Number(baud.value)});
      renderConnection(state);message(state.connected?'串口已连接，等待设备数据。':'串口已断开。');
    } catch(error) {message(error.message,true);}
    finally {busy=false;renderConnection({connected,port:port.value,baud:baud.value});}
  });
  byId('serial-refresh').addEventListener('click',refreshPorts);
  root.querySelectorAll('[data-serial-command]').forEach(button => button.addEventListener('click',() => send(button.dataset.serialCommand)));
  byId('serial-rate').addEventListener('change',event => send('RATE '+event.target.value));
  byId('serial-clear').addEventListener('click',() => {lines=[];renderLog();});
  const chartChoice=byId('serial-chart-kind');
  const savedMode=localStorage.getItem(storageKey);if(savedMode && [...chartChoice.options].some(o=>o.value===savedMode))chartChoice.value=savedMode;
  chartChoice.addEventListener('change',()=>{localStorage.setItem(storageKey,chartChoice.value);draw();});
  port.addEventListener('change',()=>localStorage.setItem('serial-port',port.value));
  baud.value=localStorage.getItem('serial-baud')||'115200';
  baud.addEventListener('change',()=>localStorage.setItem('serial-baud',baud.value));
  root.addEventListener('toggle',event => {if(event.target===root && root.open){draw();renderLog();}});
  window.addEventListener('resize',draw);
  window.addEventListener('pagehide',() => {stopped=true;});
  window.addEventListener('pageshow',event => {if(event.persisted){stopped=false;poll();}});
  async function poll() {
    if(stopped)return;
    try {
      const state=await api('status?after='+cursor);
      if(connectionId!==state.connection_id) {
        connectionId=state.connection_id;resetData();lines=[];
      }
      renderConnection(state);cursor=state.cursor;
      const frames=[...state.events,...Object.values(state.latest)].sort((a,b)=>a.id-b.id);
      frames.forEach(frame => {
        if(frame.id<=(seen[frame.type]||0))return;
        seen[frame.type]=frame.id;
        const relevant=kind==='TRK'?frame.type==='TRK':['CAP','CAPMON','CAPSTAT','TRK'].includes(frame.type);
        if(!relevant && !['TX','ACK'].includes(frame.type))return;
        const time=new Date(frame.received_at*1000).toLocaleTimeString('zh-CN',{hour12:false});
        lines.push(time+' '+(frame.type==='TX'?'发送':'接收')+' '+frame.raw);
        if(relevant)renderFrame(frame);
      });
      if(lines.length>200)lines=lines.slice(-200);renderLog();
      byId('serial-counters').textContent='接收 '+state.stats.received_bytes+' B · 发送 '+state.stats.sent_bytes+
        ' B · 校验异常 '+state.stats.invalid_frames+' · 缺失帧 '+state.stats.sequence_gaps;
      if(state.error)message(state.error,true);
      dataAge();
    } catch(error) {message('无法获取串口状态：'+error.message,true);}
    finally {if(!stopped)setTimeout(poll,document.hidden||!connected?1500:500);}
  }
  const initial=byId('serial-initial-state');if(initial){try{renderConnection(JSON.parse(initial.textContent));}catch{}}
  refreshPorts();poll();draw();
});
