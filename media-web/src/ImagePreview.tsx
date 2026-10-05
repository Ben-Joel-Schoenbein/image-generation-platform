import { useEffect, useRef, useState } from "react";

type PreviewImage = { id: string; title: string; description: string; url: string };

export default function ImagePreview({ image, onClose, onDelete, busy }: {
  image: PreviewImage;
  onClose: () => void;
  onDelete: (image: PreviewImage) => Promise<boolean>;
  busy: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current;
    const opener = document.activeElement as HTMLElement | null;
    element?.showModal();
    return () => { element?.close(); if (opener?.isConnected) opener.focus(); };
  }, []);

  async function remove() {
    setError("");
    try { if (await onDelete(image)) onClose(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Löschen fehlgeschlagen. Bitte erneut versuchen."); }
  }

  return <dialog ref={dialog} className="imagePreview" aria-labelledby="preview-title"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div className="previewHeading"><h2 id="preview-title">{image.title || "Bildvorschau"}</h2><button className="secondary" aria-label="Vorschau schließen" disabled={busy} onClick={onClose}>×</button></div>
    <img src={image.url} alt={image.title || "Ausgewähltes Bild"} />
    {image.description && <p className="previewDescription">{image.description}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="previewActions"><a href={image.url} target="_blank" rel="noreferrer">Original öffnen ↗</a><button className="danger" disabled={busy} onClick={remove}>{busy ? "Wird gelöscht …" : "Bild löschen"}</button></div>
    <p className="muted">Löschen entfernt das Bild aus allen Archivgruppen und die Datei vom Server.</p>
  </dialog>;
}
