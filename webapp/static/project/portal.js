document.addEventListener('DOMContentLoaded', () => {
  // 兼容以前保存的页内定位链接，切换到对应的独立内容页。
  const sectionsByAnchor = {
    '#tracking-background': 'background', '#capacitance-background': 'background',
    '#tracking-control-approach': 'background', '#capacitance-monitoring-approach': 'background',
    '#tracking-network': 'algorithm',
    '#simulation-validation': 'simulation',
    '#capacitance-simulation': 'simulation',
    '#tracking-results': 'experiments', '#tracking-waveforms': 'experiments', '#prototype-efficiency': 'experiments',
    '#recognition-experiments': 'experiments', '#capacitance-results': 'experiments',
    '#model-results': 'algorithm', '#tracking-overview': 'algorithm', '#recognition-pipeline': 'algorithm'
  };
  const content = document.querySelector('[data-current-section]');
  const targetSection = sectionsByAnchor[location.hash];
  if (content && targetSection && content.dataset.currentSection !== targetSection) {
    const url = new URL(location.href);
    url.searchParams.set('section', targetSection);
    location.replace(url.href);
    return;
  }
  const selector = document.getElementById('model-version');
  if (selector) selector.addEventListener('change', () => {
    document.querySelectorAll('[data-model-panel]').forEach(panel => {
      panel.hidden = panel.dataset.modelPanel !== selector.value;
    });
  });

  document.querySelectorAll('[data-open-resource]').forEach(button => {
    button.addEventListener('click', async () => {
      const feedback = document.getElementById('file-feedback');
      button.disabled = true;
      feedback.classList.remove('error');
      feedback.textContent = '正在打开本机文件…';
      try {
        const response = await fetch('/api/project/open/' + encodeURIComponent(button.dataset.openResource), {method:'POST'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '打开失败');
        feedback.textContent = data.message + '：' + data.path;
      } catch (error) {
        feedback.classList.add('error');
        feedback.textContent = error.message;
      } finally { button.disabled = false; }
    });
  });

  const copy = document.getElementById('copy-path');
  if (copy) copy.addEventListener('click', async () => {
    const feedback = document.getElementById('file-feedback');
    try {
      await navigator.clipboard.writeText(document.getElementById('file-path').textContent.trim());
      feedback.classList.remove('error');
      feedback.textContent = '文件位置已复制';
    } catch (error) {
      feedback.classList.add('error');
      feedback.textContent = '请选中文件位置后手动复制。';
    }
  });
});
