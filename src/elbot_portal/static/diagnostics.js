// Lightweight client-side helper for Lavalink/yt-dlp diagnostics.

document.addEventListener('DOMContentLoaded', () => {
  const panels = document.querySelectorAll('[data-diagnostics-endpoint]');
  if (!panels.length) {
    return;
  }

  panels.forEach((panel) => {
    const button = panel.querySelector('[data-diagnostics-trigger]');
    const statusEl = panel.querySelector('[data-diagnostics-status]');
    const outputEl = panel.querySelector('[data-diagnostics-output]');
    const endpoint = panel.getAttribute('data-diagnostics-endpoint');

    if (!button || !statusEl || !outputEl || !endpoint) {
      return;
    }

    button.addEventListener('click', async () => {
      button.disabled = true;
      statusEl.textContent = 'Collecting diagnostics…';
      statusEl.classList.remove('error');
      outputEl.textContent = '';
      outputEl.classList.add('hidden');

      try {
        const response = await fetch(endpoint);
        const payload = await response.json().catch(() => null);

        if (!response.ok || !payload || payload.status !== 'ok') {
          const message = (payload && payload.error) || `Request failed (${response.status})`;
          throw new Error(message);
        }

        const timestamp = new Date().toLocaleTimeString();
        statusEl.textContent = `Diagnostics collected at ${timestamp}.`;
        outputEl.textContent = JSON.stringify(payload.data, null, 2);
        outputEl.classList.remove('hidden');
      } catch (error) {
        const message = error instanceof Error && error.message ? error.message : 'Failed to collect diagnostics.';
        statusEl.textContent = message;
        statusEl.classList.add('error');
      } finally {
        button.disabled = false;
      }
    });
  });
});
