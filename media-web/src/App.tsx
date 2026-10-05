import { FormEvent, useMemo, useState } from "react";
import { gql, useMutation, useQuery } from "@apollo/client";

const IMAGE_STUDIO_URL = import.meta.env.VITE_IMAGE_STUDIO_URL || "https://images.example.com";

type Collection = { id: string; name: string; description: string; children: Collection[] };
type MediaImage = { id: string; filename: string; title: string; description: string; mimeType: string; size: number; width?: number; height?: number; url: string; position: number };

const COLLECTIONS = gql`query Collections { collections { id name description children { id name description children { id name description children { id name description } } } } }`;
const IMAGES = gql`query Images($collectionId: ID!) { images(collectionId: $collectionId) { id filename title description mimeType size width height url position } }`;
const CREATE_COLLECTION = gql`mutation CreateCollection($name: String!, $description: String, $parentId: ID) { createCollection(name: $name, description: $description, parentId: $parentId) { id name } }`;
const UPDATE_COLLECTION = gql`mutation UpdateCollection($id: ID!, $name: String!, $description: String) { updateCollection(id: $id, name: $name, description: $description) { id } }`;
const DELETE_COLLECTION = gql`mutation DeleteCollection($id: ID!) { deleteCollection(id: $id) }`;
const UPDATE_IMAGE = gql`mutation UpdateImage($id: ID!, $title: String!, $description: String) { updateImage(id: $id, title: $title, description: $description) { id } }`;
const DELETE_IMAGE = gql`mutation DeleteImage($id: ID!) { deleteImage(id: $id) }`;
const REMOVE_IMAGE = gql`mutation RemoveImage($collectionId: ID!, $imageId: ID!) { removeImageFromCollection(collectionId: $collectionId, imageId: $imageId) }`;
const MOVE_IMAGE = gql`mutation MoveImage($collectionId: ID!, $imageId: ID!, $position: Int!) { moveImage(collectionId: $collectionId, imageId: $imageId, position: $position) }`;

function flatten(items: Collection[], depth = 0): Array<{ item: Collection; depth: number }> {
  return items.flatMap((item) => [{ item, depth }, ...flatten(item.children || [], depth + 1)]);
}

function App() {
  const { data, loading, error } = useQuery<{ collections: Collection[] }>(COLLECTIONS);
  const collections = data?.collections || [];
  const flat = useMemo(() => flatten(collections), [collections]);
  const [selectedId, setSelectedId] = useState("");
  const active = flat.find(({ item }) => item.id === selectedId)?.item;
  const imagesResult = useQuery<{ images: MediaImage[] }>(IMAGES, { variables: { collectionId: selectedId }, skip: !selectedId });
  const images = imagesResult.data?.images || [];
  const [createCollection] = useMutation(CREATE_COLLECTION, { refetchQueries: [{ query: COLLECTIONS }] });
  const [updateCollection] = useMutation(UPDATE_COLLECTION, { refetchQueries: [{ query: COLLECTIONS }] });
  const [deleteCollection] = useMutation(DELETE_COLLECTION, { refetchQueries: [{ query: COLLECTIONS }] });
  const [updateImage] = useMutation(UPDATE_IMAGE, { refetchQueries: [{ query: IMAGES, variables: { collectionId: selectedId } }] });
  const [deleteImage] = useMutation(DELETE_IMAGE, { refetchQueries: [{ query: IMAGES, variables: { collectionId: selectedId } }] });
  const [removeImage] = useMutation(REMOVE_IMAGE, { refetchQueries: [{ query: IMAGES, variables: { collectionId: selectedId } }] });
  const [moveImage] = useMutation(MOVE_IMAGE, { refetchQueries: [{ query: IMAGES, variables: { collectionId: selectedId } }] });
  const [notice, setNotice] = useState("");

  async function submitCollection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const result = await createCollection({ variables: { name: String(form.get("name")), description: String(form.get("description") || ""), parentId: String(form.get("parentId") || "") || null } });
      const createdId = result.data?.createCollection?.id;
      if (createdId) setSelectedId(createdId);
      formElement.reset();
      setNotice("Gruppe angelegt.");
    } catch (e) { setNotice(message(e)); }
  }

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fileInput = form.elements.namedItem("file") as HTMLInputElement;
    const file = fileInput.files?.[0];
    if (!file || !selectedId) return;
    const body = new FormData();
    body.append("file", file);
    body.append("collectionId", selectedId);
    body.append("title", String(new FormData(form).get("title") || file.name.replace(/\.[^.]+$/, "")));
    body.append("description", String(new FormData(form).get("description") || ""));
    try {
      const response = await fetch("/upload", { method: "POST", body });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Upload fehlgeschlagen");
      await imagesResult.refetch();
      form.reset();
      setNotice("Bild zur Gruppe hinzugefügt.");
    } catch (e) { setNotice(message(e)); }
  }

  async function saveCollection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    const form = new FormData(event.currentTarget);
    try {
      await updateCollection({ variables: { id: active.id, name: String(form.get("name")), description: String(form.get("description") || "") } });
      setNotice("Gruppeninfos gespeichert.");
    } catch (e) { setNotice(message(e)); }
  }

  if (loading) return <main><p>Lade Archiv …</p></main>;
  if (error) return <main><h1>Bildarchiv</h1><p className="error">{error.message}</p></main>;

  return <main className="shell">
    <header className="top"><div><p className="eyebrow">FREUNDE · BILDSAMMLUNG</p><h1>Bildarchiv</h1><p className="sub">Serien, Ausgaben und Seiten an einem Ort.</p></div><a className="back" href={IMAGE_STUDIO_URL}>Zur Bildgenerierung ↗</a></header>
    {notice && <div className="notice" onClick={() => setNotice("")}>{notice}<button aria-label="Meldung schließen">×</button></div>}
    <div className="layout">
      <aside className="side">
        <section className="panel"><h2>Sammlungen</h2>{collections.length ? <nav>{collections.map((c) => <CollectionNav key={c.id} item={c} selectedId={selectedId} onSelect={setSelectedId} depth={0} />)}</nav> : <p className="muted">Noch keine Gruppen angelegt.</p>}</section>
        <section className="panel"><h2>Neue Gruppe</h2><form onSubmit={submitCollection} className="stack"><label>Name<input name="name" required maxLength={180} placeholder="z. B. Depleter Comics" /></label><label>Beschreibung<textarea name="description" rows={2} maxLength={2000} placeholder="Optional" /></label><label>Übergeordnete Gruppe<select name="parentId"><option value="">Keine · neue Serie</option>{flat.map(({ item, depth }) => <option key={item.id} value={item.id}>{"　".repeat(depth)}{item.name}</option>)}</select></label><button className="primary">Gruppe anlegen</button></form></section>
      </aside>
      <section className="content">
        {!active ? <div className="empty"><div className="emptyIcon">▧</div><h2>Wähle eine Gruppe</h2><p>Lege eine Serie an und füge darunter einzelne Ausgaben oder andere Untergruppen hinzu.</p></div> : <>
          <div className="panel collectionInfo"><div><p className="eyebrow">AUSGEWÄHLTE GRUPPE</p><h2>{active.name}</h2><p className="muted">{images.length} {images.length === 1 ? "Bild" : "Bilder"}</p></div><button className="danger ghost" onClick={async () => { if (confirm(`„${active.name}“ samt Untergruppen löschen? Bilder, die nur dort gespeichert sind, werden ebenfalls gelöscht.`)) { await deleteCollection({ variables: { id: active.id } }); setSelectedId(""); } }}>Gruppe löschen</button></div>
          <form onSubmit={saveCollection} className="panel editGroup"><label>Gruppenname<input key={`${active.id}-name`} name="name" defaultValue={active.name} required maxLength={180} /></label><label>Beschreibung<textarea key={`${active.id}-description`} name="description" rows={2} defaultValue={active.description} maxLength={2000} /></label><button className="secondary">Gruppeninfos speichern</button></form>
          <section className="panel"><h3>Bild hinzufügen</h3><form onSubmit={submitUpload} className="uploadForm"><label>Datei<input name="file" type="file" accept="image/png,image/jpeg,image/webp" required /></label><label>Titel<input name="title" maxLength={200} placeholder="Optional" /></label><label>Beschreibung<input name="description" maxLength={4000} placeholder="Optional" /></label><button className="primary">Hochladen</button><small>PNG, JPEG oder WebP · bis zu 20 MB</small></form></section>
          <section className="imageList"><div className="listHeader"><h3>Bilder / Seiten</h3><span className="muted">Pfeile ändern die Reihenfolge</span></div>{imagesResult.loading ? <p>Lade Bilder …</p> : images.length ? <div className="cards">{images.map((image, index) => <ImageCard key={image.id} image={image} index={index} total={images.length} onSave={async (title, description) => { await updateImage({ variables: { id: image.id, title, description } }); setNotice("Bildinfos gespeichert."); }} onDelete={async () => { if (confirm(`„${image.title}“ endgültig löschen?`)) { await deleteImage({ variables: { id: image.id } }); setNotice("Bild gelöscht."); } }} onRemove={async () => { if (confirm(`„${image.title}“ aus dieser Gruppe entfernen? Wenn keine weitere Gruppe mit dem Bild verknüpft ist, wird auch die Datei gelöscht.`)) { await removeImage({ variables: { collectionId: selectedId, imageId: image.id } }); setNotice("Bild aus dieser Gruppe entfernt."); } }} onMove={(position) => moveImage({ variables: { collectionId: selectedId, imageId: image.id, position } })} />)}</div> : <div className="empty smallEmpty"><p>Hier ist noch nichts. Lade Bilder in diese Gruppe hoch.</p></div>}</section>
        </>}
      </section>
    </div>
    <footer>Bilddateien liegen auf diesem Server. Gruppen, Beschreibungen und Reihenfolge liegen in PostgreSQL.</footer>
  </main>;
}

function CollectionNav({ item, selectedId, onSelect, depth }: { item: Collection; selectedId: string; onSelect: (id: string) => void; depth: number }) {
  return <div><button className={`treeItem ${selectedId === item.id ? "active" : ""}`} style={{ paddingLeft: 12 + depth * 17 }} onClick={() => onSelect(item.id)}><span>{depth ? "↳" : "▣"}</span>{item.name}</button>{item.children?.map((child) => <CollectionNav key={child.id} item={child} selectedId={selectedId} onSelect={onSelect} depth={depth + 1} />)}</div>;
}

function ImageCard({ image, index, total, onSave, onDelete, onRemove, onMove }: { image: MediaImage; index: number; total: number; onSave: (title: string, description: string) => Promise<void>; onDelete: () => Promise<void>; onRemove: () => Promise<void>; onMove: (position: number) => void }) {
  const [title, setTitle] = useState(image.title);
  const [description, setDescription] = useState(image.description);
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setSaving(true); await onSave(title, description); setSaving(false); }
  return <article className="imageCard"><a href={image.url} target="_blank" rel="noreferrer"><img src={image.url} alt={image.title} loading="lazy" /></a><div className="imageMeta"><small>{image.width && image.height ? `${image.width} × ${image.height} · ` : ""}{formatSize(image.size)}</small><form onSubmit={submit} className="stack"><label>Titel<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} /></label><label>Beschreibung<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={4000} /></label><button className="secondary" disabled={saving}>{saving ? "Speichere …" : "Speichern"}</button></form><div className="actions"><button title="Nach oben" disabled={index === 0} onClick={() => onMove(index - 1)}>↑</button><button title="Nach unten" disabled={index === total - 1} onClick={() => onMove(index + 1)}>↓</button><button className="mutedButton" onClick={onRemove}>Aus Gruppe entfernen</button><button className="danger" onClick={onDelete}>Löschen</button></div></div></article>;
}

function formatSize(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`; }
function message(error: unknown) { return error instanceof Error ? error.message : "Etwas ist schiefgelaufen."; }

export default App;
