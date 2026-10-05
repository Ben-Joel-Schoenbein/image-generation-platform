"""Private gallery preview and selected-image deletion, with retryable file cleanup."""
import asyncio
import html
import logging
import re
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Form, Request
from fastapi.responses import JSONResponse, Response

LOG = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parent


def gallery_markup(token):
    return f'''<link rel="stylesheet" href="/assets/gallery.css?v=1">
<p id="gallery-notice" role="status" aria-live="polite"></p>
<dialog id="image-preview" aria-labelledby="image-preview-title" data-csrf="{html.escape(token, quote=True)}">
<div class="preview-heading"><h2 id="image-preview-title">Bildvorschau</h2><button type="button" id="image-preview-close" aria-label="Vorschau schließen">×</button></div>
<img id="image-preview-image" alt="Ausgewähltes Bild">
<p id="image-preview-caption"></p><p id="image-preview-error" role="alert" hidden></p>
<div class="preview-actions"><a id="image-preview-original" target="_blank" rel="noopener">Original öffnen ↗</a><button type="button" id="image-preview-delete" class="gallery-danger">Bild löschen</button></div>
<p class="muted">Das Bild wird aus deiner Galerie entfernt und die Galeriedatei vom Server gelöscht.</p></dialog>
<script src="/assets/gallery.js?v=1" defer></script>'''


def image_path(data_dir, user_id, image_id):
    if not re.fullmatch(r"[0-9a-f]{32}", image_id) or not str(user_id).isdigit():
        raise ValueError("Invalid image identifier")
    root = Path(data_dir).resolve() / "images"
    candidate = root / str(user_id) / f"{image_id}.png"
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Image path is outside the gallery")
    return candidate


def cleanup_files(db, data_dir, image_id=None):
    with db() as conn:
        rows = conn.execute("SELECT image_id,user_id FROM generation_file_deletions" +
                            (" WHERE image_id=?" if image_id else ""),
                            (image_id,) if image_id else ()).fetchall()
        for row in rows:
            try:
                image_path(data_dir, row["user_id"], row["image_id"]).unlink(missing_ok=True)
                conn.execute("DELETE FROM generation_file_deletions WHERE image_id=?", (row["image_id"],))
            except (OSError, ValueError):
                LOG.exception("Gallery file cleanup will be retried for %s", row["image_id"])


def install_gallery_routes(app, db, current_user, verify_csrf, data_dir):
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application):
            with db() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS generation_file_deletions (image_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL)")

            async def retry_cleanup():
                while True:
                    try:
                        cleanup_files(db, data_dir)
                    except sqlite3.Error:
                        LOG.exception("Gallery cleanup database unavailable; retrying")
                    await asyncio.sleep(30)

            cleanup_files(db, data_dir)
            task = asyncio.create_task(retry_cleanup())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app.router.lifespan_context = lifespan

    @app.get("/assets/gallery.js")
    async def gallery_js():
        return Response((ASSETS / "gallery.js").read_text(), media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/assets/gallery.css")
    async def gallery_css():
        return Response((ASSETS / "gallery.css").read_text(), media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.post("/image/{image_id}/delete")
    async def delete_image(request: Request, image_id: str, csrf_token: str = Form("")):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "Bitte zuerst anmelden."}, status_code=401)
        try:
            verify_csrf(request, csrf_token)
        except ValueError:
            return JSONResponse({"error": "Die Sitzung ist abgelaufen. Lade die Seite neu."}, status_code=403)
        if not re.fullmatch(r"[0-9a-f]{32}", image_id):
            return JSONResponse({"error": "Bild nicht gefunden."}, status_code=404)
        try:
            with db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT image_path FROM generations WHERE id=? AND user_id=?", (image_id, user["id"])).fetchone()
                if not row:
                    return JSONResponse({"error": "Bild nicht gefunden."}, status_code=404)
                expected = image_path(data_dir, user["id"], image_id)
                if Path(row["image_path"]).resolve() != expected.resolve():
                    raise ValueError("Unexpected stored image path")
                conn.execute("INSERT OR IGNORE INTO generation_file_deletions(image_id,user_id) VALUES(?,?)", (image_id, user["id"]))
                conn.execute("DELETE FROM generations WHERE id=? AND user_id=?", (image_id, user["id"]))
        except (sqlite3.Error, OSError, ValueError):
            LOG.exception("Could not delete gallery image %s", image_id)
            return JSONResponse({"error": "Das Bild konnte nicht gelöscht werden. Bitte erneut versuchen."}, status_code=503)
        pending = True
        try:
            cleanup_files(db, data_dir, image_id)
            with db() as conn:
                pending = bool(conn.execute("SELECT 1 FROM generation_file_deletions WHERE image_id=?", (image_id,)).fetchone())
        except sqlite3.Error:
            LOG.exception("Gallery cleanup queued for %s", image_id)
        return JSONResponse({"deleted": True, "file_cleanup_pending": pending})
