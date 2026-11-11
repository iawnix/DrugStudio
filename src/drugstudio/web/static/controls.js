/* Small shared behaviours for controls. Rendering never replaces existing SVGs. */
(function () {
  'use strict';
  function icons() {
    if (!window.lucide) return;
    const placeholders = document.querySelectorAll('i[data-lucide]');
    if (!placeholders.length) return;
    placeholders.forEach(node => {
      node.setAttribute('data-icon-pending', node.getAttribute('data-lucide'));
      node.removeAttribute('data-lucide');
    });
    window.lucide.createIcons({nameAttr:'data-icon-pending',attrs:{'aria-hidden':'true',focusable:'false','stroke-width':1.75}});
    document.querySelectorAll('svg[data-icon-pending]').forEach(node => {
      node.dataset.icon = node.getAttribute('data-icon-pending');
      node.removeAttribute('data-icon-pending');
    });
  }

  // Keep focus and button geometry stable; native disabled state remains owned
  // by each form/workflow, so a completed request cannot re-enable a stale action.
  async function busy(button, action) {
    if (!button || button.getAttribute('aria-busy') === 'true') return;
    const previous = button.getAttribute('aria-disabled');
    button.setAttribute('aria-busy','true');
    button.setAttribute('aria-disabled','true');
    try { return await action(); }
    finally {
      button.removeAttribute('aria-busy');
      if (previous === null) button.removeAttribute('aria-disabled');
      else button.setAttribute('aria-disabled',previous);
    }
  }
  window.DrugStudioControls = {icons,busy};
  document.addEventListener('DOMContentLoaded', icons);
})();
