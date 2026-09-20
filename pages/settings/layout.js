'use strict';

// Move existing controls once, preserving IDs and their normal event handlers.
(() => {
  const shell = document.querySelector('.studio-shell');
  const history = document.getElementById('generation-history');
  const routing = document.getElementById('routing-heading').closest('section');
  routing.id = 'routing-settings';
  const oldConnection = document.querySelector('.connection-layout');
  function group(name, columns) {
    const band = document.createElement('div');
    band.className = `workspace-band ${name}`;
    for (const ids of columns) {
      const column = document.createElement('div');
      column.className = 'workspace-column';
      for (const id of ids) column.append(document.getElementById(id));
      band.append(column);
    }
    shell.insertBefore(band, history);
  }
  group('creation-band', [['multimodel-studio', 'style-library'], ['voice-builder', 'routing-settings']]);
  shell.insertBefore(document.getElementById('voice-library'), history);
  group('direction-band', [['director-settings'], ['session-settings', 'voice-bindings']]);
  group('delivery-band', [['delivery-settings'], ['connection-settings', 'reliability-settings']]);
  shell.insertBefore(document.getElementById('access-settings'), history);
  shell.insertBefore(document.getElementById('live-tasks'), history);
  oldConnection.remove();

  const transport = document.getElementById('audio-transport');
  function syncTransportFields() {
    for (const id of ['shared-path-linux', 'shared-path-windows']) {
      const input = document.getElementById(id);
      if (input) input.closest('label').hidden = transport.value !== 'shared_path';
    }
  }
  transport.addEventListener('change', syncTransportFields);
  window.syncStudioTransportFields = syncTransportFields;
  syncTransportFields();

  const links = [...document.querySelectorAll('.workspace-nav a')];
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting);
    if (!visible.length) return;
    const id = visible[0].target.id;
    for (const link of links) {
      if (link.hash === `#${id}`) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  }, {rootMargin: '-10% 0px -55% 0px'});
  for (const link of links) observer.observe(document.querySelector(link.hash));
})();
