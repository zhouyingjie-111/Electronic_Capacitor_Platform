/** 仿真交互：参数提交、状态轮询、模型预览和示波器选择；显示后端真实状态，按钮忙闲与任务生命周期保持同步。 */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('simulation-form');
  if (!form) return;
  const selector = document.getElementById('simulation-type');
  const duration = document.getElementById('simulation-duration');
  const durationField = document.getElementById('simulation-duration-field');
  const start = document.getElementById('simulation-start');
  const description = document.getElementById('simulation-description');
  const workspace = document.getElementById('simulation-workspace');
  const feedback = document.getElementById('simulation-feedback');
  const stop = document.getElementById('simulation-stop');
  const progress = document.getElementById('simulation-progress');
  const viewer = document.getElementById('simulation-model-viewer');
  const viewport = document.getElementById('simulation-model-viewport');
  const model = document.getElementById('simulation-model-link');
  const image = document.getElementById('simulation-model-image');
  const placeholder = document.getElementById('simulation-model-placeholder');
  const retry = document.getElementById('simulation-image-retry');
  const parameterFields = [...form.querySelectorAll('[data-simulation-parameter]')];
  const code = document.getElementById('simulation-ann-code');
  const codeState = document.getElementById('simulation-ann-code-state');
  const resetCode = document.getElementById('simulation-ann-reset');
  const scopes = document.getElementById('simulation-scopes');
  const scopeButtons = document.getElementById('simulation-scope-buttons');
  placeholder.before(scopes);
  let originalCode = null, codeLoading = false, scopeJob = null, openingScope = false;
  const active = new Set(['starting', 'loading', 'initializing', 'running', 'paused', 'stopping', 'exporting']);
  const module = form.dataset.module || 'tracking';
  const allowed = module === 'capacitance' ? ['capacitance'] : ['pi', 'ann', 'custom'];
  const durations = {pi: '0.8', ann: '0.5', custom: '0.8', capacitance:'0.1'};
  let selected = '';
  let busy = false;
  let submitting = false;
  let jobId = null;
  let latestJob = null;
  let pollTimer = null;
  let imageTimer = null;
  let imageLoading = false;
  let loadedVersion = null;
  let imageAttempts = 0;
  let imageGeneration = 0;

  function updateControls() {
    const runnable = allowed.includes(selector.value);
    selector.disabled = busy || submitting || openingScope;
    durationField.hidden = !runnable;
    duration.disabled = !runnable || busy || submitting;
    start.disabled = !runnable || busy || submitting || openingScope || (selector.value === 'ann' && (codeLoading || originalCode === null));
    document.getElementById('simulation-parameters').hidden = !runnable;
    document.getElementById('simulation-custom-parameters').hidden = selector.value !== 'custom';
    document.getElementById('simulation-ann-function').hidden = selector.value !== 'ann';
    parameterFields.forEach(input => {
      const custom = input.dataset.simulationParameter.startsWith('D_');
      input.disabled = !runnable || busy || submitting || (custom && selector.value !== 'custom');
    });
    code.disabled = selector.value !== 'ann' || busy || submitting || codeLoading;
    resetCode.disabled = busy || submitting || codeLoading;
    description.textContent = runnable ? ''
      : '请先选择仿真方式。';
    description.hidden = !description.textContent;
  }
  selector.addEventListener('change', () => {
    if (selected in durations) durations[selected] = duration.value;
    selected = selector.value;
    if (selected in durations) duration.value = durations[selected];
    feedback.textContent = '';
    if (selected === 'ann' && originalCode === null) loadCode();
    updateControls();
  });
  duration.addEventListener('input', () => {
    if (selector.value in durations) durations[selector.value] = duration.value;
  });
  function formatTime(value) {
    return typeof value === 'number' ? `${Number(value.toPrecision(6))} s` : '等待初始化';
  }
  async function request(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '操作暂未成功，请重试。');
    return data;
  }
  async function loadCode() {
    if (codeLoading) return;
    codeLoading = true; codeState.textContent = '正在读取模型原有代码…'; updateControls();
    try {
      const result = await request('/api/project/simulation/ann-function');
      originalCode = result.code; code.value = originalCode;
      codeState.textContent = '已载入模型原有代码';
    } catch (error) { codeState.textContent = error.message; }
    finally { codeLoading = false; updateControls(); }
  }
  resetCode.addEventListener('click', () => {
    if (originalCode === null) loadCode();
    else { code.value = originalCode; codeState.textContent = '已恢复模型原有代码'; }
  });
  code.addEventListener('input', () => { codeState.textContent = code.value === originalCode ? '已载入模型原有代码' : '已编辑，将替换本次仿真的函数代码'; });
  function setImageSize() {
    viewport.scrollTop = viewport.scrollLeft = 0;
  }

  function renderScopes(job) {
    window.portalScopeViewer?.setJob(job);
    scopes.hidden = !job.scopes_ready || job.state !== 'completed';
    if (scopeJob !== job.id) {
      scopeJob = job.id; scopeButtons.replaceChildren();
      document.getElementById('simulation-scope-feedback').textContent = '';
    }
    if (!scopes.hidden && !scopeButtons.children.length) {
      job.scopes.forEach(scope => {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'btn'; button.textContent = `${scope.name}示波器`;
        button.dataset.scopeId = scope.id;
        button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', async () => {
          if (job.waveforms_ready) {
            window.portalScopeViewer?.show(job, scope);
            return;
          }
          if (openingScope) return;
          const targetJob = job.id;
          const status = document.getElementById('simulation-scope-feedback');
          openingScope = true; updateControls();
          [...scopeButtons.children].forEach(item => { item.disabled = true; });
          status.textContent = `正在打开${scope.name}示波器…`;
          try {
            const result = await request(`/api/project/simulation/${targetJob}/scope/${scope.id}/open`, {method:'POST'});
            if (scopeJob === targetJob) status.textContent = result.message;
          } catch (error) { if (scopeJob === targetJob) status.textContent = error.message; }
          finally {
            openingScope = false; updateControls();
            if (scopeJob === targetJob) [...scopeButtons.children].forEach(item => { item.disabled = false; });
          }
        });
        scopeButtons.append(button);
      });
    }
    [...scopeButtons.children].forEach(button => { button.disabled = openingScope || job.finished_at === null; });
    const scopeFeedback = document.getElementById('simulation-scope-feedback');
    if (!job.waveforms_ready && job.waveforms_message && !scopeFeedback.textContent) scopeFeedback.textContent = job.waveforms_message;
  }

  async function loadDiagram(job, force = false) {
    const version = `${job.id}:${job.diagram_version}`;
    if (!job.diagram_ready || imageLoading || (!force && loadedVersion === version)) return;
    clearTimeout(imageTimer);
    imageLoading = true;
    const generation = imageGeneration;
    const attempt = ++imageAttempts;
    const url = `/api/project/simulation/${job.id}/diagram?v=${encodeURIComponent(job.diagram_version)}&attempt=${attempt}`;
    placeholder.hidden = false;
    placeholder.textContent = '正在完整加载模型画面…';
    try {
      // Download a fresh complete file and decode it off-screen. Never reveal
      // a partially received image in the visible model viewer.
      const response = await fetch(url, {cache: 'no-store'});
      if (!response.ok) throw new Error('模型画面暂不可用');
      const objectUrl = URL.createObjectURL(await response.blob());
      const prepared = new Image();
      try {
        prepared.src = objectUrl;
        await prepared.decode();
        if (prepared.naturalWidth !== job.diagram_width || prepared.naturalHeight !== job.diagram_height) {
          throw new Error('模型图像尺寸校验失败');
        }
        if (generation !== imageGeneration || job.id !== jobId) return;
        image.src = objectUrl;
        await image.decode();
        if (generation !== imageGeneration || job.id !== jobId) return;
        image.alt = `${job.label} Simulink 模型完整画面`;
        model.href = url;
        viewport.hidden = false;
        viewer.hidden = false;
        placeholder.hidden = true;
        retry.hidden = true;
        loadedVersion = version;
      } finally {
        // A decoded image retains its pixels after the object URL is released.
        URL.revokeObjectURL(objectUrl);
      }
    } catch (error) {
      if (generation === imageGeneration && job.id === jobId) {
        placeholder.textContent = '模型画面加载失败，正在重新获取完整图片…';
        if (attempt < 3) imageTimer = setTimeout(() => loadDiagram(job, true), 1500);
        else {
          placeholder.textContent = '模型画面暂未加载成功，请点击重新加载。';
          viewer.hidden = false;
          retry.hidden = false;
        }
      }
    } finally {
      if (generation === imageGeneration) imageLoading = false;
    }
  }
  retry.addEventListener('click', () => {
    if (latestJob) { imageAttempts = 0; loadDiagram(latestJob, true); }
  });

  function render(job) {
    if (jobId !== job.id) {
      imageGeneration++;
      clearTimeout(imageTimer);
      viewer.hidden = true;
      model.removeAttribute('href');
      image.removeAttribute('src');
      viewport.hidden = true;
      placeholder.hidden = false;
      placeholder.textContent = '模型画面将在完整导出后显示';
      imageLoading = false;
      loadedVersion = null;
      imageAttempts = 0;
      setImageSize(false);
    }
    jobId = job.id;
    latestJob = job;
    renderScopes(job);
    workspace.hidden = false;
    document.getElementById('simulation-heading').textContent = `${job.label} · Simulink 仿真`;
    document.getElementById('simulation-state').textContent = job.message;
    document.getElementById('simulation-percent').textContent = job.progress === null ? '' : `${job.progress}%`;
    if (job.progress === null && active.has(job.state)) progress.removeAttribute('value');
    else progress.value = job.progress ?? 0;
    document.getElementById('simulation-time').textContent = formatTime(job.simulation_time);
    document.getElementById('simulation-target').textContent = formatTime(job.stop_time);
    document.getElementById('simulation-elapsed').textContent = `${job.elapsed_seconds} s`;
    workspace.dataset.state = job.state;
    busy = active.has(job.state);
    updateControls();
    stop.hidden = !busy || job.state === 'exporting';
    stop.disabled = job.state === 'stopping';
    if (job.diagram_ready && imageAttempts < 3) loadDiagram(job);
    if (job.state === 'failed') feedback.textContent = job.message;
    feedback.classList.toggle('error', job.state === 'failed');
    return busy || job.finished_at === null;
  }
  async function poll() {
    clearTimeout(pollTimer);
    if (!jobId) return;
    const requestedJob = jobId;
    try {
      const job = await request(`/api/project/simulation/${requestedJob}`);
      if (requestedJob !== jobId) return;
      feedback.textContent = '';
      if (render(job)) pollTimer = setTimeout(poll, 900);
    } catch (error) {
      if (requestedJob !== jobId) return;
      feedback.textContent = `进度连接暂时中断，正在重连。${error.message}`;
      feedback.classList.add('error');
      pollTimer = setTimeout(poll, 3000);
    }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || submitting || !form.reportValidity()) return;
    submitting = true;
    updateControls();
    feedback.classList.remove('error');
    feedback.textContent = '正在提交仿真任务…';
    try {
      const parameters = {};
      // Inputs are disabled while submitting; choose fields by model instead.
      parameterFields.forEach(input => {
        if (!input.dataset.simulationParameter.startsWith('D_') || selector.value === 'custom') parameters[input.dataset.simulationParameter] = input.valueAsNumber;
      });
      const job = await request('/api/project/simulation/start', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({control: selector.value, duration: duration.valueAsNumber, parameters,
          ...(selector.value === 'ann' ? {ann_code: code.value} : {})})
      });
      feedback.textContent = '';
      render(job);
      workspace.scrollIntoView({block: 'start'});
      poll();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add('error');
    } finally {
      submitting = false;
      updateControls();
    }
  });
  stop.addEventListener('click', async () => {
    if (!jobId) return;
    stop.disabled = true;
    try { render(await request(`/api/project/simulation/${jobId}/stop`, {method: 'POST'})); }
    catch (error) { feedback.textContent = error.message; stop.disabled = false; }
  });
  updateControls();
  request('/api/project/simulation/current?module='+module).then(data => {
    if (data.job && !jobId) {
      selector.value = data.job.control;
      selected = data.job.control;
      parameterFields.forEach(input => { if (data.job.parameters && input.dataset.simulationParameter in data.job.parameters) input.value = data.job.parameters[input.dataset.simulationParameter]; });
      if (selected === 'ann') loadCode();
      duration.value = data.job.requested_duration ?? durations[selected];
      if (render(data.job)) poll();
    }
  }).catch(() => { feedback.textContent = '仿真状态暂不可用，点击开始仿真可重新尝试。'; });
  window.addEventListener('pagehide', () => {
    clearTimeout(pollTimer);
    clearTimeout(imageTimer);
    imageGeneration++;
  });
});
