(() => {
  const dialog = document.getElementById('image-preview');
  if (!dialog) return;
  const image = document.getElementById('image-preview-image');
  const caption = document.getElementById('image-preview-caption');
  const original = document.getElementById('image-preview-original');
  const close = document.getElementById('image-preview-close');
  const remove = document.getElementById('image-preview-delete');
  const error = document.getElementById('image-preview-error');
  const notice = document.getElementById('gallery-notice');
  const generated = document.getElementById('generation-result');
  if (generated) {
    generated.tabIndex = 0; generated.setAttribute('role', 'button'); generated.setAttribute('aria-label', 'Generiertes Bild ansehen');
    generated.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); generated.click(); }
    });
  }
  let selected = null, busy = false, opener = null;
  const imageId = link => {
    const url = new URL(link.href, location.href);
    return url.origin === location.origin && /^\/image\/([0-9a-f]{32})$/.exec(url.pathname)?.[1];
  };
  document.addEventListener('click', event => {
    const resultTrigger = event.target.closest('#generation-result, #generation-result-link');
    const link = resultTrigger ? document.getElementById('generation-result-link') : event.target.closest('a[href]');
    if (!link || (!resultTrigger && !link.querySelector('img')) || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const id = imageId(link);
    if (!id || busy) return;
    event.preventDefault();
    selected = id; opener = resultTrigger || link;
    image.src = '/image/' + id;
    original.href = image.src;
    caption.textContent = link.closest('article')?.querySelector('small')?.textContent || 'Generiertes Bild';
    error.hidden = true; notice.textContent = '';
    if (!dialog.open) dialog.showModal();
  });
  close.addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('click', event => { if (event.target === dialog && !busy) dialog.close(); });
  dialog.addEventListener('close', () => { image.removeAttribute('src'); selected = null; if (opener?.isConnected) opener.focus(); });
  remove.addEventListener('click', async () => {
    if (!selected || busy || !window.confirm('Dieses Bild endgültig aus deiner Galerie löschen und die Galeriedatei vom Server entfernen?')) return;
    const id = selected;
    busy = true; remove.disabled = true; close.disabled = true;
    remove.textContent = 'Wird gelöscht …'; error.hidden = true;
    try {
      const response = await fetch('/image/' + id + '/delete', {
        method: 'POST', credentials: 'same-origin',
        body: new URLSearchParams({csrf_token: dialog.dataset.csrf})
      });
      const result = await response.json();
      if (!response.ok && response.status !== 404) throw new Error(result.error || 'Löschen fehlgeschlagen.');
      if (response.ok && result.deleted !== true) throw new Error('Löschen konnte nicht bestätigt werden.');
      const gallery = document.getElementById('generation-gallery');
      gallery?.querySelectorAll('article').forEach(article => {
        const link = article.querySelector('a[href]');
        if (link && imageId(link) === id) article.remove();
      });
      if (gallery && !gallery.querySelector('article')) {
        const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'Noch keine Bilder.'; gallery.replaceChildren(empty);
      }
      const resultLink = document.getElementById('generation-result-link');
      if (resultLink && imageId(resultLink) === id) {
        resultLink.hidden = true; resultLink.removeAttribute('href');
        if (generated) { generated.hidden = true; generated.removeAttribute('src'); }
      }
      notice.textContent = result.file_cleanup_pending ? 'Bild gelöscht. Die Datei wird beim nächsten Bereinigungsversuch entfernt.' : 'Bild gelöscht.';
      dialog.close();
      if (!opener?.isConnected) { notice.tabIndex = -1; notice.focus(); }
    } catch (failure) {
      error.textContent = failure.message || 'Löschen fehlgeschlagen. Bitte erneut versuchen.'; error.hidden = false;
    } finally {
      busy = false; remove.disabled = false; close.disabled = false; remove.textContent = 'Bild löschen';
    }
  });
})();
