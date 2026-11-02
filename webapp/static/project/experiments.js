document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-experiment-group]').forEach(group => {
    const owned = selector => [...group.querySelectorAll(selector)].filter(node => node.closest('[data-experiment-group]') === group);
    const panels = owned('[data-experiment-panel]');
    const buttons = owned('[data-experiment-target]');
    const select = owned('[data-experiment-select]')[0];
    let trigger = null;
    function show(id) {
      const valid = panels.some(panel => panel.id === id);
      panels.forEach(panel => { panel.hidden = !valid || panel.id !== id; });
      buttons.forEach(button => {
        if (!button.hasAttribute('aria-pressed')) return;
        const selected = valid && button.dataset.experimentTarget === id;
        button.setAttribute('aria-pressed', String(selected));
        button.setAttribute('aria-expanded', String(selected));
        button.setAttribute('aria-controls', button.dataset.experimentTarget);
      });
      owned('[data-experiment-empty]').forEach(node => { node.hidden = valid; });
      if (select) select.value = valid ? id : '';
    }
    buttons.forEach(button => button.addEventListener('click', () => {
      if (button.dataset.experimentTarget) trigger = button;
      show(button.dataset.experimentTarget);
      if (!button.dataset.experimentTarget) trigger?.focus();
    }));
    select?.addEventListener('change', () => show(select.value));
    // Explicit saved section links can still open their corresponding record.
    function fromHash() {
      const target = document.getElementById(location.hash.slice(1));
      const panel = panels.find(item => item === target || item.contains(target));
      if (panel) {
        const records = panel.closest('.experiment-records');
        if (records) records.open = true;
        show(panel.id);
      }
    }
    show('');
    fromHash();
    window.addEventListener('hashchange', fromHash);
  });
  document.querySelectorAll('[data-experiment-position]').forEach(select => {
    select.addEventListener('change', () => {
      select.closest('[data-experiment-panel]').querySelectorAll('[data-position]').forEach(row => {
        row.hidden = select.value !== 'all' && row.dataset.position !== select.value;
      });
    });
  });
});
