/** ANN 工作台：训练、CPU 编译、NPU 编译分别管理状态和下载产物，所选数据决定预览来源。 */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('ann-training-form');
  if (!form) return;
  const byId = id => document.getElementById(id);
  const source = byId('ann-dataset-source');
  const datasetFile = byId('ann-dataset-file');
  const modelSource = byId('ann-model-source');
  const modelFile = byId('ann-model-file');
  const start = byId('ann-start');
  const compile = byId('ann-compile');
  const compileTarget = byId('ann-compile-target');
  const feedback = byId('ann-feedback');
  const compileFeedback = byId('ann-compile-feedback');
  const log = byId('ann-log');
  let datasetToken = null, modelToken = null;
  let datasetName = '';
  const selectionKey = 'ann-dataset-selection';
  function rememberDataset() {
    try { sessionStorage.setItem(selectionKey, JSON.stringify({source: source.value, token: datasetToken, name: datasetName})); } catch {}
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(selectionKey) || 'null');
    if (saved?.source === 'upload' && saved.token) {
      source.value = 'upload'; datasetToken = saved.token; datasetName = saved.name || '';
      feedback.textContent = `已选择：${datasetName}`;
    }
  } catch {}
  let busy = false, uploading = 0, hasTraining = false, hasNpuSource = false, renderedResult = null;
  let jobId = null, timer = null, previewGeneration = 0;
  let compilationJob = null;
  const cursors = new Map();
  const logJobs = {training: null, compile: null};
  const active = new Set(['queued', 'training', 'compiling']);
  async function request(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    let data;
    try { data = await response.json(); } catch { throw new Error('服务暂不可用，请稍后重试。'); }
    if (!response.ok) throw new Error(data.error || '操作失败，请重试。');
    return data;
  }
  function fail(error, target = feedback) { target.textContent = error.message; target.classList.add('error'); }
  function controls() {
    [source, datasetFile, modelSource, modelFile, compileTarget].forEach(control => { control.disabled = busy || uploading > 0; });
    start.disabled = busy || uploading > 0 || (source.value === 'upload' && !datasetToken);
    const npu = compileTarget.value === 'npu';
    modelSource.options[0].textContent=npu?'使用最近训练或已保存模型':'使用本次训练输出';
    compile.disabled = busy || uploading > 0 || (modelSource.value === 'upload' ? !modelToken : !(hasTraining || npu && hasNpuSource)) || (npu && source.value==='upload' && !datasetToken);
    compile.textContent = npu ? 'NPU 加速编译' : '编译模型';
    byId('ann-npu-results').hidden = !(npu && compilationJob?.target === 'npu' && compilationJob.compilation_completed && compilationJob.npu_report);
    byId('ann-dataset-file-field').hidden = source.value !== 'upload';
    byId('ann-model-file-field').hidden = modelSource.value !== 'upload';
  }
  function preview(data) {
    const table = document.querySelector('.data-preview table');
    if (!table) return;
    const header = document.createElement('thead'), row = document.createElement('tr');
    data.columns.forEach(name => { const cell = document.createElement('th'); cell.textContent = name; row.append(cell); });
    header.append(row);
    const body = document.createElement('tbody');
    data.rows.forEach(values => { const row = document.createElement('tr'); values.forEach(value => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }); body.append(row); });
    table.replaceChildren(header, body);
  }
  async function refreshPreview() {
    const generation = ++previewGeneration;
    const table = document.querySelector('.data-preview table');
    const token = source.value === 'default' ? 'default' : datasetToken;
    table?.replaceChildren();
    table?.removeAttribute('aria-busy');
    if (!token) return;
    table?.setAttribute('aria-busy', 'true');
    try {
      const data = await request(`/api/project/training/preview?dataset=${encodeURIComponent(token)}`);
      if (generation === previewGeneration) preview(data);
    } catch (error) {
      if (generation === previewGeneration) {
        if (source.value === 'upload') { datasetToken = null; datasetName = ''; rememberDataset(); controls(); }
        fail(error);
      }
    } finally { if (generation === previewGeneration) table?.removeAttribute('aria-busy'); }
  }
  source.addEventListener('change', () => {
    feedback.classList.remove('error');
    feedback.textContent = source.value === 'upload' && datasetToken ? `已选择：${datasetName}` : '';
    rememberDataset(); controls(); refreshPreview();
  });
  modelSource.addEventListener('change', () => { compileFeedback.textContent = ''; controls(); });
  compileTarget.addEventListener('change', () => { compileFeedback.textContent = ''; controls(); });
  async function upload(kind, input) {
    const uploadFeedback = kind === 'model' ? compileFeedback : feedback;
    const file = input.files[0];
    if (kind === 'dataset') { datasetToken = null; datasetName = ''; rememberDataset(); previewGeneration++; document.querySelector('.data-preview table')?.replaceChildren(); }
    else modelToken = null;
    if (!file) { controls(); return; }
    uploading++;
    controls();
    uploadFeedback.classList.remove('error'); uploadFeedback.textContent = '正在读取文件…';
    try {
      const body = new FormData(); body.append('file', file);
      const data = await request(`/api/project/training/upload/${kind}`, {method: 'POST', body});
      if (kind === 'dataset') { datasetToken = data.token; datasetName = data.name; rememberDataset(); await refreshPreview(); }
      else modelToken = data.token;
      uploadFeedback.textContent = `已选择：${data.name}`;
    } catch (error) { fail(error, uploadFeedback); }
    finally { uploading--; controls(); }
  }
  datasetFile.addEventListener('change', () => upload('dataset', datasetFile));
  modelFile.addEventListener('change', () => upload('model', modelFile));
  function results(job) {
    if (!job.training_completed || !job.metrics) return;
    hasTraining = true;
    byId('ann-results').hidden = false;
    if (renderedResult !== job.id) {
      const metric = job.metrics.test_metrics.DTgiven;
      const row = document.createElement('tr');
      ['DTgiven', ...['mae', 'mse', 'r2', 'max_absolute_error'].map(name => Number.isFinite(metric[name]) ? Number(metric[name]).toPrecision(6) : '—')].forEach(value => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
      byId('ann-metrics').replaceChildren(row);
      const figures = byId('ann-result-figures'); figures.replaceChildren();
      [['test_true_vs_predicted.png', 'DTgiven · 预测散点图'], ['test_prediction_comparison.png', 'DTgiven · 逐样本预测对比']].forEach(([filename, label]) => {
        const figure = document.createElement('figure'), link = document.createElement('a'), image = document.createElement('img'), caption = document.createElement('figcaption');
        link.href = `/api/project/training/${job.id}/artifact/${filename}`; link.dataset.imageViewer = '';
        image.src = link.href; image.alt = label; image.loading = 'lazy';
        caption.textContent = label; link.append(image); figure.append(link, caption); figures.append(figure);
      });
      renderedResult = job.id;
    }
    byId('ann-download').hidden = active.has(job.state);
    byId('ann-download').href = `/api/project/training/${job.id}/download?kind=model`;
  }
  function render(job) {
    const target = job.kind === 'compile' ? byId('ann-compile-log') : log;
    const stage = job.kind === 'compile' ? byId('ann-compile-stage') : byId('ann-stage');
    if (logJobs[job.kind] !== job.id) { logJobs[job.kind] = job.id; target.textContent = ''; }
    const follow = target.scrollHeight - target.scrollTop - target.clientHeight < 70;
    if (job.logs.length && job.next_cursor > (cursors.get(job.id) || 0)) target.textContent += job.logs.join('\n') + '\n';
    cursors.set(job.id, job.next_cursor);
    if (follow) target.scrollTop = target.scrollHeight;
    busy = active.has(job.state);
    hasTraining = hasTraining || job.has_training_output === true;
    stage.textContent = `${job.message} · ${job.elapsed_seconds} s`;
    results(job);
    if (job.kind === 'compile' && job.compilation_completed) {
      byId('ann-compile-download').hidden = false;
      byId('ann-compile-download').href = `/api/project/training/${job.id}/download?kind=compile`;
      byId('ann-compile-download').textContent = job.target==='npu' ? '下载 NPU 编译结果' : '下载编译结果';
    }
    if(job.kind==='compile') {
      compilationJob = job;
      const report=job.npu_report;
      if(report && job.compilation_completed) {
        byId('ann-npu-mapping').textContent=`${report.hardware_layers} / ${report.total_dense_layers} 层 · NPU`;
        const rows=[['浮点模型与参考值',report.float_metrics],['量化模型与参考值',report.quantized_metrics],['量化前后偏差',report.quantization_delta]];
        byId('ann-npu-metrics').replaceChildren(...rows.map(([label,metric])=>{
          const row=document.createElement('tr');[label,...['mae','rmse','max_abs_error'].map(k=>Number.isFinite(metric[k])?metric[k].toPrecision(6):'—')].forEach(v=>{const cell=document.createElement('td');cell.textContent=v;row.append(cell);});return row;
        }));
        byId('ann-npu-status').textContent=`${report.calibration_samples} 条校准样本 · ${report.comparison_samples} 条留出对比样本 · 已编译，待上板验证`;
      }
    }
    if (job.state.endsWith('failed')) fail(new Error(job.message), job.kind === 'compile' ? compileFeedback : feedback);
    controls();
    return busy || job.has_more;
  }
  async function poll() {
    clearTimeout(timer);
    const expected = jobId;
    try {
      const job = await request(`/api/project/training/${expected}?after=${cursors.get(expected) || 0}`);
      if (expected !== jobId) return;
      if (render(job)) timer = setTimeout(poll, job.has_more ? 50 : 1200);
    } catch (error) { fail(error, logJobs.compile === expected ? compileFeedback : feedback); timer = setTimeout(poll, 3000); }
  }
  async function begin(compileOnly) {
    if (busy || uploading) return;
    const taskFeedback = compileOnly ? compileFeedback : feedback;
    busy = true; controls(); clearTimeout(timer);
    taskFeedback.classList.remove('error'); taskFeedback.textContent = '正在提交任务…';
    const data = {dataset: source.value === 'default' ? 'default' : datasetToken};
    if (compileOnly && modelSource.value === 'upload') data.model = modelToken;
    if (compileOnly) {data.target=compileTarget.value;if(data.target!=='npu')delete data.dataset;}
    try {
      const job = await request(`/api/project/training/${compileOnly ? 'compile' : 'start'}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      if (!compileOnly) {
        byId('ann-results').hidden = true; renderedResult = null;
        byId('ann-compile-log').textContent = '';
        byId('ann-compile-stage').textContent = '等待开始';
      }
      byId('ann-compile-download').hidden = true;
      if(compileOnly)byId('ann-npu-results').hidden=true;
      taskFeedback.textContent = ''; jobId = job.id; render(job); poll();
    } catch (error) { busy = false; fail(error, taskFeedback); controls(); }
  }
  form.addEventListener('submit', event => { event.preventDefault(); if (!start.disabled) begin(false); });
  compile.addEventListener('click', () => { if (!compile.disabled) begin(true); });
  request('/api/project/training/current').then(data => {
    if (jobId) return;
    hasNpuSource=data.has_npu_source===true;
    if(data.compile_job)compileTarget.value=data.compile_job.target||'standard';
    if (data.training_job) render(data.training_job);
    if (data.compile_job) render(data.compile_job);
    if (data.job) { jobId = data.job.id; if (render(data.job)) poll(); }
    controls();
  }).catch(fail);
  window.addEventListener('pagehide', () => clearTimeout(timer));
  controls();
  refreshPreview();
});
