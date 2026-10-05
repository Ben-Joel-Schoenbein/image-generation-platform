(() => {
  'use strict';
  const form = document.getElementById('generation-form');
  if (!form) return;
  const button = document.getElementById('generation-submit');
  const panel = document.getElementById('generation-progress');
  const label = document.getElementById('generation-label');
  const bar = document.getElementById('generation-bar');
  const detail = document.getElementById('generation-detail');
  const message = document.getElementById('generation-message');
  const timer = document.getElementById('generation-time');
  const result = document.getElementById('generation-result');
  const resultLink = document.getElementById('generation-result-link');
  const storageKey = 'image-studio-generation';
  let busy = false, snapshot = null, observed = Date.now();
  let attempt = 0;
  function remember(id) { try { sessionStorage.setItem(storageKey, id); } catch (_) {} }
  function remembered() { try { return sessionStorage.getItem(storageKey); } catch (_) { return null; } }
  function forget() { try { sessionStorage.removeItem(storageKey); } catch (_) {} }
  function setBusy(value) {
    busy = value;
    button.disabled = value;
    button.textContent = value ? 'Generating…' : 'Generate';
    form.setAttribute('aria-busy', String(value));
  }
  function draw(job) {
    snapshot = job; observed = Date.now();
    panel.hidden = false; panel.dataset.state = job.state;
    label.textContent = job.label;
    detail.textContent = job.detail || '';
    message.textContent = job.error || '';
    if (job.percent === null || job.percent === undefined) bar.removeAttribute('value');
    else bar.value = job.percent;
    const terminal = job.state === 'done' || job.state === 'error';
    setBusy(!terminal);
    if (terminal) {
      forget();
      if (job.state === 'done' && /^\/image\/[a-f0-9]{32}$/.test(job.result_url || '')) {
        result.src = job.result_url; result.hidden = false;
        resultLink.href = job.result_url; resultLink.hidden = false;
        message.textContent = 'Saved to your gallery.';
      }
    }
  }
  function localError(text, unlock = true) {
    panel.hidden = false; panel.dataset.state = 'error';
    label.textContent = 'Generation status'; message.textContent = text;
    bar.removeAttribute('value');
    if (unlock) { setBusy(false); forget(); snapshot = null; }
  }
  async function read(url, options) {
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('Server response could not be read.'); }
    if (!response.ok) {
      const error = new Error(data.error || (data.detail ? 'Please check the generation form.' : 'Request failed.'));
      error.status = response.status;
      throw error;
    }
    return data;
  }
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function poll(id, token, acceptanceDeadline = 0) {
    while (token === attempt) {
      try {
        const data = await read('/generation-jobs/' + encodeURIComponent(id));
        if (token !== attempt) return;
        draw(data.job);
        if (data.job.state === 'done' || data.job.state === 'error') return;
      } catch (error) {
        if (token !== attempt) return;
        if (error.status === 404 && Date.now() < acceptanceDeadline) {
          await pause(1000); continue;
        }
        if (error.status === 401 || error.status === 404) {
          localError(error.message); return;
        }
        // Don't unlock or submit another job after a temporary network failure.
        message.textContent = 'Connection interrupted. Reconnecting to progress…';
      }
      await pause(1000);
    }
  }
  setInterval(() => {
    const seconds = snapshot ? snapshot.elapsed + (busy ? Math.floor((Date.now() - observed) / 1000) : 0) : 0;
    timer.textContent = Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
  }, 1000);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const token = ++attempt;
    setBusy(true); panel.hidden = false; panel.dataset.state = 'running';
    result.hidden = true; resultLink.hidden = true;
    label.textContent = 'Uploading and validating your request';
    message.textContent = ''; detail.textContent = ''; bar.removeAttribute('value');
    snapshot = {elapsed: 0}; observed = Date.now();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const id = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    const payload = new FormData(form);
    payload.set('generation_job_id', id);
    remember(id);
    try {
      const data = await read('/generation-jobs', {method: 'POST', body: payload});
      if (token !== attempt) return;
      remember(data.job.id); draw(data.job);
      if (busy) await poll(data.job.id, token);
    } catch (error) {
      if (token !== attempt) return;
      if (error.status) { localError(error.message); return; }
      // A lost submission response is ambiguous. Look up the known request ID;
      // do not retry the POST or start a duplicate generation.
      message.textContent = 'Checking whether your request was accepted…';
      await pause(1500);
      await poll(id, token, Date.now() + 60000);
    }
  });
  async function restore() {
    setBusy(true);
    try {
      const data = await read('/generation-jobs/active');
      const id = data.job ? data.job.id : remembered();
      if (id) {
        remember(id);
        if (data.job) draw(data.job);
        await poll(id, ++attempt);
      } else setBusy(false);
    } catch (error) {
      if (error.status) localError(error.message);
      else {
        localError('Connection interrupted. Checking for a running generation…', false);
        setTimeout(restore, 2000);
      }
    }
  }
  restore();
})();
