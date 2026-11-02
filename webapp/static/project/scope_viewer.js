/** 波形交互：维护时间窗口、缩放/平移和游标，按需从后端读取降采样数据；不更改真实模型或设备参数。 */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('scope-web-viewer');
  if (!root) return;
  const byId = id => document.getElementById(id);
  const plots = byId('scope-plots'), status = byId('scope-web-status');
  const colors = ['#3979b5', '#b77b38', '#548b72', '#8b6ba6', '#b55765', '#697b41'];
  let job = null, scope = null, data = null, bounds = null, range = null;
  let enabled = new Set(), charts = [], generation = 0, controller = null, timer = null;
  let mode = 'pan', drag = null, cursor = null;
  const observer = new ResizeObserver(() => draw());

  function number(value) { return Number(value.toPrecision(7)).toString(); }
  function setJob(next) {
    if (job?.id === next.id) { job = next; return; }
    job = next; scope = null; data = null; root.hidden = true;
    controller?.abort(); generation++; clearTimeout(timer);
    observer.disconnect(); plots.replaceChildren(); charts = [];
  }
  function setMode(next) {
    mode = next;
    byId('scope-pan').setAttribute('aria-pressed', String(mode === 'pan'));
    byId('scope-select').setAttribute('aria-pressed', String(mode === 'select'));
    charts.forEach(chart => { chart.canvas.style.cursor = mode === 'pan' ? 'grab' : 'crosshair'; });
  }
  function clampRange(first, last) {
    const full = bounds[1] - bounds[0];
    const span = Math.min(full, Math.max(full * 1e-7, last - first));
    first = Math.max(bounds[0], Math.min(bounds[1] - span, first));
    return [first, first + span];
  }
  function updateRange(next, fetch = true) {
    controller?.abort(); generation++; clearTimeout(timer);
    range = clampRange(...next);
    byId('scope-start').value = number(range[0]);
    byId('scope-end').value = number(range[1]);
    draw();
    if (fetch) timer = setTimeout(load, 160);
  }
  async function load() {
    if (!job || !scope || !range) return;
    controller?.abort(); controller = new AbortController();
    const expected = ++generation, target = [...range];
    status.textContent = '正在读取当前时间范围的波形…';
    try {
      const query = new URLSearchParams({start:target[0], end:target[1], points:3000});
      const response = await fetch(`/api/project/simulation/${job.id}/scope/${scope.id}/waveform?${query}`, {cache:'no-store', signal:controller.signal});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '波形读取失败。');
      if (expected !== generation || target[0] !== range[0] || target[1] !== range[1]) return;
      data = result;
      plots.dataset.rangeStart = target[0]; plots.dataset.rangeEnd = target[1];
      if (!charts.length) build();
      charts.forEach(chart => { chart.channels = result.channels.filter(channel => channel.group === chart.group); });
      status.textContent = '波形已就绪';
      draw();
    } catch (error) {
      if (expected === generation && error.name !== 'AbortError') status.textContent = error.message;
    }
  }
  async function show(next, selected) {
    setJob(next);
    if (scope?.id === selected.id && data) { root.hidden = false; return; }
    scope = selected; data = null; bounds = [next.start_time, next.stop_time]; range = [...bounds];
    cursor = null; drag = null; charts = []; observer.disconnect(); plots.replaceChildren();
    byId('scope-channels').replaceChildren(); byId('scope-cursor').textContent = '移动鼠标查看波形读数';
    byId('scope-web-title').textContent = `${selected.name}示波器`;
    document.querySelectorAll('[data-scope-id]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.scopeId === selected.id)));
    root.hidden = false;
    updateRange(range, false);
    await load();
  }
  function build() {
    enabled = new Set(data.channels.map(channel => channel.id));
    const choices = byId('scope-channels'); choices.replaceChildren();
    data.channels.forEach((channel, index) => {
      const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
      input.type = 'checkbox'; input.checked = true; input.value = channel.id;
      text.textContent = channel.label; label.style.setProperty('--channel-color', colors[index % colors.length]);
      input.addEventListener('change', () => {
        if (input.checked) enabled.add(channel.id); else enabled.delete(channel.id);
        byId('scope-cursor').textContent = enabled.size ? '移动鼠标查看波形读数' : '请选择至少一个通道';
        draw();
      });
      label.append(input, text); choices.append(label);
    });
    [...new Set(data.channels.map(channel => channel.group))].forEach(group => {
      const box = document.createElement('div'), canvas = document.createElement('canvas');
      box.className = 'scope-plot'; canvas.tabIndex = 0;
      canvas.setAttribute('aria-label', `${scope.name}示波器 · 输入 ${group} 波形，可使用滚轮缩放和拖动平移`);
      canvas.setAttribute('role', 'img'); box.append(canvas); plots.append(box);
      const chart = {group, box, canvas, channels:data.channels.filter(channel => channel.group === group)};
      charts.push(chart); observer.observe(box); interactions(chart);
    });
    setMode(mode);
  }
  function geometry(chart) {
    const width = chart.box.clientWidth, height = 215;
    return {width, height, left:76, right:width - 24, top:30, bottom:height - 38};
  }
  function xTime(chart, clientX) {
    const rect = chart.canvas.getBoundingClientRect(), area = geometry(chart);
    const fraction = Math.max(0, Math.min(1, (clientX - rect.left - area.left) / (area.right - area.left)));
    return range[0] + fraction * (range[1] - range[0]);
  }
  function nearest(channel, time) {
    let left = 0, right = channel.time.length;
    while (left < right) { const middle = (left + right) >> 1; if (channel.time[middle] < time) left = middle + 1; else right = middle; }
    if (!left) return 0;
    if (left === channel.time.length) return left - 1;
    return time - channel.time[left - 1] <= channel.time[left] - time ? left - 1 : left;
  }
  function draw() {
    if (!data || !range) return;
    charts.forEach(chart => {
      const selected = chart.channels.filter(channel => enabled.has(channel.id));
      chart.box.hidden = !selected.length;
      if (!selected.length) return;
      const area = geometry(chart);
      if (area.width < 150) return;
      const ratio = window.devicePixelRatio || 1;
      chart.canvas.width = Math.round(area.width * ratio); chart.canvas.height = area.height * ratio;
      chart.canvas.style.height = `${area.height}px`;
      const context = chart.canvas.getContext('2d'); context.scale(ratio, ratio);
      context.fillStyle = '#fff'; context.fillRect(0, 0, area.width, area.height);
      let low = Infinity, high = -Infinity;
      selected.forEach(channel => channel.values.forEach((value, i) => {
        if (value !== null && channel.time[i] >= range[0] && channel.time[i] <= range[1]) { low = Math.min(low, value); high = Math.max(high, value); }
      }));
      if (!Number.isFinite(low)) { low = -1; high = 1; }
      const padding = (high - low || Math.max(1, Math.abs(high)) * .1) * .1;
      low -= padding; high += padding;
      const x = value => area.left + (value - range[0]) / (range[1] - range[0]) * (area.right - area.left);
      const y = value => area.bottom - (value - low) / (high - low) * (area.bottom - area.top);
      context.font = '12px "Times New Roman", "SimSun", serif';
      context.lineWidth = 1; context.textBaseline = 'middle';
      for (let i = 0; i <= 4; i++) {
        const py = area.top + (area.bottom - area.top) * i / 4;
        context.strokeStyle = '#e6edf4'; context.beginPath(); context.moveTo(area.left, py); context.lineTo(area.right, py); context.stroke();
        context.fillStyle = '#667b91'; context.textAlign = 'right'; context.fillText(Number((high - (high - low) * i / 4).toPrecision(5)).toString(), area.left - 10, py);
      }
      for (let i = 0; i <= 4; i++) {
        const px = area.left + (area.right - area.left) * i / 4;
        context.strokeStyle = '#e6edf4'; context.beginPath(); context.moveTo(px, area.top); context.lineTo(px, area.bottom); context.stroke();
        context.fillStyle = '#667b91'; context.textAlign = 'center'; context.fillText(Number((range[0] + (range[1] - range[0]) * i / 4).toPrecision(5)).toString(), px, area.bottom + 15);
      }
      context.textAlign = 'left'; context.fillStyle = '#405a75'; context.font = '13px "Times New Roman", "SimSun", serif';
      context.fillText(selected.map(channel => channel.label).join(' / '), area.left, 14);
      context.textAlign = 'right'; context.fillText('时间 / s', area.right, area.height - 7);
      context.save(); context.beginPath(); context.rect(area.left, area.top, area.right - area.left, area.bottom - area.top); context.clip();
      selected.forEach(channel => {
        const colorIndex = data.channels.findIndex(item => item.id === channel.id);
        context.strokeStyle = colors[colorIndex % colors.length]; context.lineWidth = 1.25; context.beginPath(); let connected = false;
        channel.time.forEach((time, i) => {
          const value = channel.values[i];
          if (value === null) { connected = false; return; }
          if (!connected) context.moveTo(x(time), y(value)); else context.lineTo(x(time), y(value));
          connected = true;
        });
        context.stroke();
      });
      if (cursor !== null) { context.strokeStyle = '#7790aa'; context.setLineDash([3, 3]); context.beginPath(); context.moveTo(x(cursor), area.top); context.lineTo(x(cursor), area.bottom); context.stroke(); context.setLineDash([]); }
      if (drag?.chart === chart && mode === 'select') {
        context.fillStyle = '#3979b52a'; context.fillRect(x(Math.min(drag.time, drag.current)), area.top, Math.abs(x(drag.current) - x(drag.time)), area.bottom - area.top);
      }
      context.restore();
    });
  }
  function interactions(chart) {
    const canvas = chart.canvas;
    canvas.addEventListener('wheel', event => {
      event.preventDefault(); if (!data || drag) return;
      const anchor = xTime(chart, event.clientX), factor = Math.exp(Math.max(-1, Math.min(1, event.deltaY * .002)));
      updateRange([anchor - (anchor - range[0]) * factor, anchor + (range[1] - anchor) * factor]);
    }, {passive:false});
    canvas.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      canvas.setPointerCapture(event.pointerId);
      drag = {chart, clientX:event.clientX, range:[...range], time:xTime(chart, event.clientX), current:xTime(chart, event.clientX)};
    });
    canvas.addEventListener('pointermove', event => {
      if (drag?.chart === chart) {
        if (mode === 'pan') {
          const area = geometry(chart), shift = (event.clientX - drag.clientX) / (area.right - area.left) * (drag.range[1] - drag.range[0]);
          updateRange([drag.range[0] - shift, drag.range[1] - shift], false);
        } else { drag.current = xTime(chart, event.clientX); draw(); }
        return;
      }
      cursor = xTime(chart, event.clientX);
      const readings = data.channels.filter(channel => enabled.has(channel.id) && channel.time.length).map(channel => {
        const i = nearest(channel, cursor), value = channel.values[i];
        return `${channel.label}：${value === null ? '—' : number(value)}（t=${number(channel.time[i])} s）`;
      });
      byId('scope-cursor').textContent = readings.join('　｜　'); draw();
    });
    canvas.addEventListener('pointerup', event => {
      if (!drag) return;
      const previous = drag; drag = null;
      if (mode === 'select' && Math.abs(event.clientX - previous.clientX) > 5) updateRange([Math.min(previous.time, previous.current), Math.max(previous.time, previous.current)]);
      else if (mode === 'pan') updateRange(range);
      draw();
    });
    canvas.addEventListener('pointercancel', () => { drag = null; updateRange(range); });
    canvas.addEventListener('pointerleave', () => { if (!drag) { cursor = null; draw(); } });
    canvas.addEventListener('dblclick', () => updateRange(bounds));
    canvas.addEventListener('keydown', event => {
      const span = range[1] - range[0], center = (range[0] + range[1]) / 2;
      if (event.key === 'Home') updateRange(bounds);
      else if (event.key === '+' || event.key === '=') updateRange([center - span / 4, center + span / 4]);
      else if (event.key === '-') updateRange([center - span, center + span]);
      else if (event.key === 'ArrowLeft') updateRange([range[0] - span * .2, range[1] - span * .2]);
      else if (event.key === 'ArrowRight') updateRange([range[0] + span * .2, range[1] + span * .2]);
      else return;
      event.preventDefault();
    });
  }
  byId('scope-pan').addEventListener('click', () => setMode('pan'));
  byId('scope-select').addEventListener('click', () => setMode('select'));
  byId('scope-reset').addEventListener('click', () => { cursor = null; updateRange(bounds); });
  byId('scope-apply').addEventListener('click', () => {
    const first = byId('scope-start').valueAsNumber, last = byId('scope-end').valueAsNumber;
    if (!Number.isFinite(first) || !Number.isFinite(last) || first >= last || last < bounds[0] || first > bounds[1]) { status.textContent = '请输入有效的起止时间范围。'; return; }
    updateRange([first, last]);
  });
  byId('scope-native').addEventListener('click', async () => {
    const button = byId('scope-native'), expected = job.id;
    button.disabled = true; status.textContent = '正在打开本机示波器…';
    try {
      const response = await fetch(`/api/project/simulation/${expected}/scope/${scope.id}/open`, {method:'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      if (job.id === expected) status.textContent = result.message;
    } catch (error) { if (job.id === expected) status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  window.addEventListener('pagehide', () => { controller?.abort(); clearTimeout(timer); observer.disconnect(); });
  window.portalScopeViewer = {setJob, show};
});
