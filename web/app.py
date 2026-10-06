import asyncio
# IMAGE_STUDIO_PROGRESS_V1
import base64
import hashlib
import html
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
# IMAGE_STUDIO_IMAGE_DELETE_V1
from image_gallery import gallery_markup, install_gallery_routes
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from PIL import Image
from qwen_quality import configure_workflow, prepare_prompt, validate_options
# QWEN_RAPID_AIO_V19_INTEGRATION_V1
from rapid_aio import is_rapid_model, build_rapid_workflow, output_dimensions, rapid_controls, validate_generation_model
from lora_support import CONFIG_KEY as LORA_CONFIG_KEY, parse_config as parse_lora_config, read_config as read_lora_config, selected_loras, install_lora_routes
# IMAGE_STUDIO_HERETIC_PE_V1
from heretic_client import resolve_prompt_expansion, rewrite_prompt, capture_prompt
from generation_progress import JOBS, ProgressMonitor, comfy_get, progress_markup, install_progress_routes
from fastapi.responses import JSONResponse
# QWEN_21_QUALITY_UPGRADE_V1
from starlette.middleware.sessions import SessionMiddleware


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "studio.sqlite3"
COMFY_URL = os.getenv("COMFY_URL", "http://comfyui:8188").rstrip("/")
WORKFLOW_TEXT = Path(os.getenv("WORKFLOW_TEXT", "/workflows/text2img.api.json"))
WORKFLOW_EDIT = Path(os.getenv("WORKFLOW_EDIT", "/workflows/img2img.api.json"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))
MAX_REFERENCES = 10
MAX_REFERENCE_TOTAL_MB = int(os.getenv("MAX_REFERENCE_TOTAL_MB", "60"))
MAX_PROMPT_CHARS = 2000
GENERATION_LOCK = asyncio.Lock()


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        raw = base64.b64decode(stored)
        return secrets.compare_digest(hash_password(password, raw[:16]), stored)
    except (ValueError, TypeError):
        return False


def initialize():
    username = os.getenv("ADMIN_USERNAME", "admin").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    if len(password) < 14:
        raise RuntimeError("Set ADMIN_PASSWORD to a unique password with at least 14 characters")
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (LORA_CONFIG_KEY, json.dumps(parse_lora_config())))
        conn.execute("CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, image_path TEXT NOT NULL, prompt TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id))")
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            conn.execute("INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,1)", (username, hash_password(password)))
        defaults = {
            "blocked_terms": "[]",
            "allow_text": "true",
            "allow_edit": "true",
            "max_prompt_chars": str(MAX_PROMPT_CHARS),
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))


def setting(key, fallback=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else fallback


def set_setting(key, value):
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, token: str):
    if not secrets.compare_digest(request.session.get("csrf", ""), token or ""):
        raise ValueError("The form expired. Refresh the page and try again.")


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    with db() as conn:
        return conn.execute("SELECT id,username,is_admin FROM users WHERE id=?", (uid,)).fetchone()


def page(title: str, body: str, user=None, notice="", csrf_token="") -> HTMLResponse:
    nav = ""
    if user:
        nav = f'<div class="nav"><span>Signed in as <b>{html.escape(user["username"])}</b></span><a href="/">Gallery</a>'
        if user["is_admin"]:
            nav += '<a href="/admin">Admin settings</a><a href="/admin/loras">LoRAs</a>'
        nav += '<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{{CSRF}}"><button class="link">Sign out</button></form></div>'
    notice_html = f'<p class="notice">{html.escape(notice)}</p>' if notice else ""
    doc = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · Image Studio</title>
<style>body{{margin:0;background:#10131b;color:#edf0f7;font:16px system-ui,sans-serif}}main{{max-width:960px;margin:40px auto;padding:24px}}.card{{background:#1b2030;border:1px solid #30384e;border-radius:14px;padding:22px;margin:18px 0}}input,textarea,select{{width:100%;box-sizing:border-box;background:#111522;color:#fff;border:1px solid #46506a;border-radius:8px;padding:12px;margin:7px 0 15px}}input[type=checkbox]{{width:auto}}button,.button{{background:#7968ff;color:#fff;border:0;border-radius:8px;padding:11px 18px;font-weight:700;cursor:pointer;text-decoration:none}}.link{{background:none;padding:0;color:#bbb}}.nav{{display:flex;gap:18px;align-items:center;border-bottom:1px solid #30384e;padding-bottom:14px;flex-wrap:wrap}}.nav form{{margin-left:auto}}a{{color:#bcb4ff}}.notice{{background:#273b32;padding:12px;border-radius:8px}}.error{{background:#482929;padding:12px;border-radius:8px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px}}.grid img{{width:100%;border-radius:10px}}small,.muted{{color:#abb2c3}}label{{display:block}}.inline{{display:inline}}</style></head><body><main><h1>Image Studio</h1>{nav}{notice_html}{body}</main></body></html>'''
    if user:
        doc = doc.replace("{{CSRF}}", csrf_token)
    return HTMLResponse(doc)


def normalize(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()


def blocked_match(prompt: str) -> str | None:
    try:
        terms = json.loads(setting("blocked_terms", "[]"))
    except json.JSONDecodeError:
        terms = []
    normalized_prompt = f" {normalize(prompt)} "
    for term in terms:
        term_norm = normalize(term)
        if term_norm and f" {term_norm} " in normalized_prompt:
            return term
    return None


def replace_values(value, replacements):
    if isinstance(value, dict):
        return {k: replace_values(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_values(v, replacements) for v in value]
    if isinstance(value, str):
        if value == "{{SEED}}":
            return int(replacements["{{SEED}}"])
        for old, new in replacements.items():
            value = value.replace(old, new)
    return value


@dataclass
class ReferenceImage:
    raw: bytes
    filename: str
    content_type: str


class ReferenceError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def validate_reference(raw: bytes) -> ReferenceImage:
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ReferenceError(f"Each reference image must be at most {MAX_UPLOAD_MB} MB", 413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            formats = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
            if image.format not in formats:
                raise ValueError("Unsupported image format")
            extension, mime = formats[image.format]
            image.verify()
    except Exception as exc:
        raise ReferenceError("Upload a valid PNG, JPEG, or WebP reference image", 415) from exc
    return ReferenceImage(raw, f"studio-{uuid.uuid4().hex}.{extension}", mime)


def validate_reference_set(mode: str, references: list[ReferenceImage]):
    if len(references) > MAX_REFERENCES:
        raise ReferenceError(f"Use at most {MAX_REFERENCES} reference images")
    if mode == "edit" and not references:
        raise ReferenceError("Upload at least one reference image for edit mode")
    if mode == "text" and references:
        raise ReferenceError("Choose edit mode to use reference images")
    if sum(len(image.raw) for image in references) > MAX_REFERENCE_TOTAL_MB * 1024 * 1024:
        raise ReferenceError(f"Reference images together must be at most {MAX_REFERENCE_TOTAL_MB} MB", 413)


def validate_selected_model(model, mode, references, rapid_steps, quality="standard"):
    try:
        validate_generation_model(model, mode, len(references), rapid_steps)
        if is_rapid_model(model):
            source_size = None
            if references:
                with Image.open(io.BytesIO(references[0].raw)) as first_image:
                    source_size = first_image.size
            output_dimensions(quality, source_size)
    except ValueError as exc:
        raise ReferenceError(str(exc)) from exc


def build_workflow(template: dict, uploads: list[dict], prompt: str) -> dict:
    replacements = {"{{PROMPT}}": prompt, "{{SEED}}": str(secrets.randbelow(2**32))}
    image_keys = ["{{IMAGE}}"] + ["{{IMAGE" + str(i) + "}}" for i in range(2, MAX_REFERENCES + 1)]
    available_slots = {value for node in template.values() for value in node.get("inputs", {}).values()
                       if isinstance(value, str) and value in image_keys}
    if len(uploads) > len(available_slots):
        raise RuntimeError("The installed edit workflow has too few reference inputs. Install the updated Qwen workflow.")
    for index, key in enumerate(image_keys):
        upload = uploads[index] if index < len(uploads) else {}
        name = upload.get("name", "")
        subfolder = upload.get("subfolder", "")
        replacements[key] = f"{subfolder}/{name}" if subfolder and name else name
        subfolder_key = "{{IMAGE_SUBFOLDER}}" if index == 0 else "{{IMAGE" + str(index + 1) + "_SUBFOLDER}}"
        replacements[subfolder_key] = subfolder
    workflow = replace_values(template, replacements)
    # Unused optional LoadImage nodes must not be sent to ComfyUI with empty filenames.
    missing = {node_id for node_id, node in template.items()
               if node.get("class_type") == "LoadImage" and node.get("inputs", {}).get("image") in image_keys
               and not workflow[node_id]["inputs"]["image"]}
    for node_id in missing:
        workflow.pop(node_id)
    for node in workflow.values():
        for name, value in list(node.get("inputs", {}).items()):
            if isinstance(value, list) and len(value) == 2 and value[0] in missing:
                if re.fullmatch(r"image(?:[2-9]|10)", name) or re.fullmatch(r"images\.image_?\d+", name):
                    del node["inputs"][name]
                else:
                    raise RuntimeError("The workflow needs a reference image. Choose edit mode and upload an image.")
    return workflow


MODEL_FOLDERS = {"ckpt_name": "checkpoints", "unet_name": "diffusion_models", "clip_name": "text_encoders",
                 "vae_name": "vae", "lora_name": "loras"}


def validate_workflow(workflow: dict, schema: dict):
    errors = []
    for node_id, node in workflow.items():
        kind = node.get("class_type")
        metadata = schema.get(kind)
        if not metadata:
            errors.append(f"Node {node_id}: {kind} is missing. Rebuild the ComfyUI image.")
            continue
        inputs = node.get("inputs", {})
        required = metadata.get("input", {}).get("required", {})
        optional = metadata.get("input", {}).get("optional", {})
        for name in required:
            # QWEN_21_OPTIONAL_REFERENCES_FIX
            if kind == "TextEncodeQwenImage21" and name == "images":
                continue
            if name not in inputs and not any(key.startswith(name + ".") for key in inputs):
                errors.append(f"Node {node_id} ({kind}): required input {name} is missing.")
        for name, value in inputs.items():
            if isinstance(value, list) and len(value) == 2:
                upstream = workflow.get(value[0])
                if upstream is None:
                    errors.append(f"Node {node_id}: input {name} points to missing node {value[0]}.")
                continue
            field = required.get(name) or optional.get(name)
            if field and isinstance(field[0], list) and value not in field[0]:
                if name in MODEL_FOLDERS:
                    errors.append(f"Model missing: models/{MODEL_FOLDERS[name]}/{value}. Download it before generating.")
                else:
                    errors.append(f"Node {node_id} ({kind}): invalid {name}={str(value)[:120]}.")
    if errors:
        raise RuntimeError("Workflow check failed: " + " ".join(errors[:8]))


def comfy_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"ComfyUI returned HTTP {response.status_code}: {response.text[:300]}"
    messages = []
    error = data.get("error", {})
    if isinstance(error, dict):
        messages.append(str(error.get("message", "Workflow rejected")))
        if error.get("details"):
            messages.append(str(error["details"]))
    for node_id, node in data.get("node_errors", {}).items():
        for error in node.get("errors", []):
            messages.append(f"Node {node_id}: {error.get('message', '')}: {error.get('details', '')}")
    return "ComfyUI rejected the workflow: " + " ".join(messages)[:1100]


async def render_image(mode: str, prompt: str, references: list[ReferenceImage] | None = None, *,
                       quality: str = "standard", edit_intent: str = "edit", enhance_prompt: bool | None = None, seed: int | None = None, progress=None, model: str = "qwen21", rapid_steps: int = 4, prompt_expansion: str = "auto") -> bytes:
    references = references or []
    selection = resolve_prompt_expansion(prompt_expansion, enhance_prompt, model)
    enhance_prompt = selection == 'standard'
    if progress:
        progress.prompt_expansion = selection
    try:
        validate_options(quality, edit_intent, seed)
        validate_generation_model(model, mode, len(references), rapid_steps)
    except ValueError as exc:
        raise ReferenceError(str(exc)) from exc
    prompt = prepare_prompt(mode, prompt, edit_intent)
    validate_reference_set(mode, references)
    workflow_path = WORKFLOW_TEXT if mode == "text" else WORKFLOW_EDIT
    if (model == "qwen21" or enhance_prompt) and not workflow_path.is_file():
        raise FileNotFoundError("Admin has not installed this workflow")
    if progress:
        progress.update("waiting", "Waiting for the image worker")
    async with GENERATION_LOCK:
        if progress:
            progress.update("preparing", "Preparing models and references")
        async with httpx.AsyncClient(timeout=30) as client:
            if selection == 'heretic':
                prompt = await rewrite_prompt(mode, prompt, references, client, COMFY_URL, seed=seed, progress=progress)
                blocked = blocked_match(prompt)
                if blocked:
                    raise ReferenceError(f'Expanded prompt blocked by an admin rule ({blocked})')
            uploads = []
            for reference in references:
                uploaded = await client.post(f"{COMFY_URL}/upload/image", files={"image": (reference.filename, reference.raw, reference.content_type)})
                uploaded.raise_for_status()
                uploads.append(uploaded.json())
            info = await comfy_get(client, f"{COMFY_URL}/object_info", job=progress)
            info.raise_for_status()
            schema = info.json()
            if is_rapid_model(model):
                try:
                    loras = selected_loras(schema, read_lora_config(db))
                except ValueError as exc:
                    raise ReferenceError(str(exc)) from exc
                source_size = None
                if references:
                    with Image.open(io.BytesIO(references[0].raw)) as first_image:
                        source_size = first_image.size
                enhancer_template = build_workflow(json.loads(workflow_path.read_text()), uploads, prompt) if enhance_prompt else None
                workflow = build_rapid_workflow(mode, uploads, prompt, quality, seed, rapid_steps,
                                                source_size, enhancer_template, model=model, loras=loras)
            else:
                workflow = build_workflow(json.loads(workflow_path.read_text()), uploads, prompt)
                workflow = configure_workflow(workflow, prompt, quality, enhance_prompt, seed)
            if selection == 'standard':
                capture_prompt(workflow, model)
            validate_workflow(workflow, schema)
            async with ProgressMonitor(COMFY_URL, workflow, progress) as monitor:
                queued = await client.post(f"{COMFY_URL}/prompt", json={"prompt": workflow, "client_id": monitor.client_id})
                if queued.is_error:
                    raise RuntimeError(comfy_error(queued))
                queued.raise_for_status()
                prompt_id = queued.json()["prompt_id"]
                monitor.bind(prompt_id)
                result_image = await wait_for_image(client, prompt_id, progress=progress)
                if progress:
                    progress.update("saving", "Retrieving the finished image")
                image_response = await comfy_get(client, f"{COMFY_URL}/view", params=result_image, job=progress)
                image_response.raise_for_status()
                return image_response.content


def check_policy(mode: str, prompt: str) -> str | None:
    try:
        limit = int(setting("max_prompt_chars", str(MAX_PROMPT_CHARS)))
    except ValueError:
        limit = MAX_PROMPT_CHARS
    if not prompt or len(prompt) > min(max(limit, 1), MAX_PROMPT_CHARS):
        return "Prompt is empty or too long"
    if mode not in {"text", "edit"}:
        return "Invalid generation mode"
    if mode == "text" and setting("allow_text", "true") != "true":
        return "Text generation is disabled by the admin"
    if mode == "edit" and setting("allow_edit", "true") != "true":
        return "Image editing is disabled by the admin"
    match = blocked_match(prompt)
    if match:
        return f"Prompt blocked by an admin rule ({match})"
    return None


async def wait_for_image(client: httpx.AsyncClient, prompt_id: str, timeout: int = 3600, progress=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        await asyncio.sleep(2)
        response = await comfy_get(client, f"{COMFY_URL}/history/{prompt_id}", job=progress)
        response.raise_for_status()
        data = response.json().get(prompt_id, {})
        if progress and progress.stage == "queued" and not data:
            queue_response = await comfy_get(client, f"{COMFY_URL}/queue", job=progress)
            queue_response.raise_for_status()
            running = queue_response.json().get("queue_running", [])
            if any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in running):
                progress.update("processing", "The image worker is processing your request")
        status = data.get("status", {})
        if status.get("status_str") == "error":
            for event, details in status.get("messages", []):
                if event == "execution_error" and isinstance(details, dict):
                    raise RuntimeError("ComfyUI execution failed: " + str(details.get("exception_message", "Unknown error"))[:1100])
            raise RuntimeError("ComfyUI reported an error while running the workflow")
        outputs = data.get("outputs", {})
        preview = outputs.get("900", {}).get("text", [])
        if progress and preview and isinstance(preview[0], str):
            progress.expanded_prompt = preview[0]
        for node in outputs.values():
            for image in node.get("images", []):
                return image
    raise TimeoutError("Image generation took too long. Check the model worker and try again.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield


app = FastAPI(title="Friends Image Studio", lifespan=lifespan)
secret = os.getenv("SESSION_SECRET", "")
if len(secret) < 32:
    raise RuntimeError("Set SESSION_SECRET to at least 32 random characters")
app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", https_only=os.getenv("COOKIE_HTTPS_ONLY", "true").lower() == "true", max_age=60 * 60 * 24 * 7)
install_progress_routes(app, current_user)
install_gallery_routes(app, db, current_user, verify_csrf, DATA_DIR)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    body = f'''<section class="card"><h2>Sign in</h2>{f'<p class="error">{html.escape(error)}</p>' if error else ''}<form method="post" action="/login"><input type="hidden" name="csrf_token" value="{csrf(request)}"><label>Username<input name="username" required autocomplete="username"></label><label>Password<input type="password" name="password" required autocomplete="current-password"></label><button>Sign in</button></form></section>'''
    return page("Sign in", body)


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    try:
        verify_csrf(request, csrf_token)
    except ValueError:
        return RedirectResponse("/login?error=Refresh%20the%20page", status_code=303)
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return RedirectResponse("/login?error=Invalid%20username%20or%20password", status_code=303)
    request.session.clear()
    request.session["uid"] = user["id"]
    request.session["csrf"] = secrets.token_urlsafe(24)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    try:
        verify_csrf(request, csrf_token)
    except ValueError:
        pass
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, notice: str = "", error: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    text_allowed = setting("allow_text", "true") == "true"
    edit_allowed = setting("allow_edit", "true") == "true"
    modes = "".join(f'<option value="{v}">{label}</option>' for v, label, allowed in [("text", "Create from description", text_allowed), ("edit", "Edit using a reference image", edit_allowed)] if allowed)
    blocked = len(json.loads(setting("blocked_terms", "[]")))
    body = f'''<section class="card"><h2>Generate an image</h2><p class="muted">Prompts are checked against {blocked} admin-defined blocked phrase(s). Max prompt length: {html.escape(setting("max_prompt_chars", str(MAX_PROMPT_CHARS)))} characters.</p>{f'<p class="error">{html.escape(error)}</p>' if error else ''}<form id="generation-form" method="post" action="/generate" enctype="multipart/form-data"><input type="hidden" name="csrf_token" value="{csrf(request)}"><label>Mode<select name="mode">{modes}</select></label>{rapid_controls()}<label>Reference use<select name="edit_intent"><option value="edit">Edit the reference image</option><option value="recreate">Create a new depiction of referenced subjects</option></select></label><label>Output size<select name="quality"><option value="standard">Standard — about 1K</option><option value="high">High — about 2K (slower)</option></select></label><label>Prompt expansion<select name="prompt_expansion" id="prompt-expansion"><option value="off">Off</option><option value="standard" selected>Standard</option><option value="heretic">Heretic</option></select></label><p class="muted">Heretic uses the text or reference-image rewriter for the selected mode. Expansion adds waiting time. The expanded description appears below.</p><details id="expanded-prompt-panel" hidden><summary>Expanded description</summary><pre id="expanded-prompt-text" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details><label>Seed (optional)<input type="number" name="seed" min="0" max="4294967295" placeholder="Leave empty for a random result"></label><p class="muted">For a new style or pose, choose a new depiction and describe what should stay recognizable. Reference output follows Image 1's aspect ratio; text output is square.</p><label>Description<textarea name="prompt" rows="4" maxlength="{html.escape(setting("max_prompt_chars", str(MAX_PROMPT_CHARS)))}" required placeholder="Describe the image or the change you want"></textarea></label><label>Reference images (1–10 for edit mode)<input id="references" type="file" name="references" accept="image/png,image/jpeg,image/webp" multiple></label><p class="muted">Use “Image 1”, “Image 2”, etc. in your prompt. {MAX_UPLOAD_MB} MB per image, {MAX_REFERENCE_TOTAL_MB} MB total.</p><ol id="reference-list"></ol><script>const picker=document.getElementById("references");picker.addEventListener("change",()=>{{const files=Array.from(picker.files);picker.setCustomValidity(files.length>10?"Choose at most 10 reference images":"");const list=document.getElementById("reference-list");list.replaceChildren();files.forEach((file,index)=>{{const row=document.createElement("li");row.textContent="Image "+(index+1)+": "+file.name;list.appendChild(row);}});}});</script><button id="generation-submit">Generate</button></form>{progress_markup()}</section>'''
    with db() as conn:
        rows = conn.execute("SELECT id,prompt,created_at FROM generations WHERE user_id=? ORDER BY created_at DESC LIMIT 24", (user["id"],)).fetchall()
    images = "".join(f'<article><a href="/image/{row["id"]}"><img loading="lazy" src="/image/{row["id"]}"></a><small>{html.escape(row["prompt"][:160])}</small></article>' for row in rows)
    body += f'<section class="card"><h2>Your recent images</h2><div id="generation-gallery" class="grid">{images or "<p class=muted>No images yet.</p>"}</div></section>'
    body += gallery_markup(csrf(request))
    return page("Home", body, user, notice, csrf(request))


# IMAGE_STUDIO_PROGRESS_V1_ROUTES
@app.post("/generation-jobs")
async def create_generation_job(request: Request, mode: str = Form(...), prompt: str = Form(...),
                                csrf_token: str = Form(...), generation_job_id: str = Form(...),
                                references: list[UploadFile] | None = File(None), reference: UploadFile | None = File(None),
                                quality: str = Form("standard"), edit_intent: str = Form("edit"),
                                enhance_prompt: bool = Form(False), seed: int | None = Form(None), model: str = Form("qwen21"), rapid_steps: int = Form(4), prompt_expansion: str = Form("auto")):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Sign in again before generating."}, status_code=401)
    files = [item for item in (references or []) + ([reference] if reference else []) if item.filename]
    try:
        verify_csrf(request, csrf_token)
        prompt = prompt.strip()
        policy_error = check_policy(mode, prompt)
        if policy_error:
            return JSONResponse({"error": policy_error}, status_code=400)
        validate_options(quality, edit_intent, seed)
        existing = JOBS.get(generation_job_id, user["id"]) or JOBS.active(user["id"])
        if existing:
            return JSONResponse({"job": existing.snapshot()}, status_code=202)
        if len(files) > MAX_REFERENCES:
            raise ReferenceError(f"Use at most {MAX_REFERENCES} reference images")
        images = []
        for upload in files:
            images.append(validate_reference(await upload.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)))
            validate_reference_set("edit", images)
        resolve_prompt_expansion(prompt_expansion, enhance_prompt, model)
        validate_reference_set(mode, images)
        validate_selected_model(model, mode, images, rapid_steps, quality)
        job, created = JOBS.create(user["id"], generation_job_id)
        if created:
            async def operation(progress):
                png = await render_image(mode, prompt, images, quality=quality, edit_intent=edit_intent,
                                         enhance_prompt=enhance_prompt, seed=seed, progress=progress, model=model, rapid_steps=rapid_steps, prompt_expansion=prompt_expansion)
                progress.update("saving", "Saving to your gallery")
                image_id = uuid.uuid4().hex
                user_dir = DATA_DIR / "images" / str(user["id"])
                user_dir.mkdir(parents=True, exist_ok=True)
                dest = user_dir / f"{image_id}.png"
                dest.write_bytes(png)
                with db() as conn:
                    conn.execute("INSERT INTO generations(id,user_id,image_path,prompt,created_at) VALUES(?,?,?,?,?)",
                                 (image_id, user["id"], str(dest), prompt, int(time.time())))
                return f"/image/{image_id}"
            JOBS.launch(job, operation)
        return JSONResponse({"job": job.snapshot()}, status_code=202)
    except (ReferenceError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=getattr(exc, "status_code", 400))
    finally:
        for upload in files:
            await upload.close()


@app.post("/generate")
async def generate(request: Request, mode: str = Form(...), prompt: str = Form(...), csrf_token: str = Form(...),
                   references: list[UploadFile] | None = File(None), reference: UploadFile | None = File(None),
                   quality: str = Form("standard"), edit_intent: str = Form("edit"),
                   enhance_prompt: bool = Form(False), seed: int | None = Form(None), model: str = Form("qwen21"), rapid_steps: int = Form(4), prompt_expansion: str = Form("auto")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        verify_csrf(request, csrf_token)
    except ValueError as exc:
        return RedirectResponse("/?error=" + quote(str(exc)), status_code=303)
    prompt = prompt.strip()
    policy_error = check_policy(mode, prompt)
    if policy_error:
        return RedirectResponse("/?error=" + quote(policy_error), status_code=303)
    files = [item for item in (references or []) + ([reference] if reference else []) if item.filename]
    try:
        if len(files) > MAX_REFERENCES:
            raise ReferenceError(f"Use at most {MAX_REFERENCES} reference images")
        images = []
        for upload in files:
            images.append(validate_reference(await upload.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)))
            validate_reference_set("edit", images)
        resolve_prompt_expansion(prompt_expansion, enhance_prompt, model)
        validate_reference_set(mode, images)
        validate_selected_model(model, mode, images, rapid_steps, quality)
        image_bytes = await render_image(mode, prompt, images, quality=quality, edit_intent=edit_intent, enhance_prompt=enhance_prompt, seed=seed, model=model, rapid_steps=rapid_steps, prompt_expansion=prompt_expansion)
        image_id = uuid.uuid4().hex
        user_dir = DATA_DIR / "images" / str(user["id"])
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = user_dir / f"{image_id}.png"
        dest.write_bytes(image_bytes)
        with db() as conn:
            conn.execute("INSERT INTO generations(id,user_id,image_path,prompt,created_at) VALUES(?,?,?,?,?)", (image_id, user["id"], str(dest), prompt, int(time.time())))
        return RedirectResponse("/?notice=Image%20ready", status_code=303)
    except (ReferenceError, httpx.HTTPError, KeyError, json.JSONDecodeError, TimeoutError, OSError, RuntimeError) as exc:
        return RedirectResponse("/?error=" + quote(f"Generation failed: {str(exc)[:1400]}"), status_code=303)
    finally:
        for upload in files:
            await upload.close()


class BotReference(BaseModel):
    image_b64: str
    filename: str = "reference.png"


class BotGeneration(BaseModel):
    discord_user_id: str
    generation_job_id: str | None = None
    mode: str
    prompt: str
    image_b64: str | None = None
    filename: str = "reference.png"
    references: list[BotReference] = Field(default_factory=list, max_length=MAX_REFERENCES)
    quality: str = "standard"
    edit_intent: str = "edit"
    enhance_prompt: bool | None = None
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    model: str = "qwen21"
    rapid_steps: int = 4
    prompt_expansion: str = "auto"


# IMAGE_STUDIO_PROGRESS_V1_DISCORD_ROUTES
def discord_jobs_authorized(request):
    expected = os.getenv("DISCORD_BOT_SECRET", "")
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    return bool(expected and secrets.compare_digest(expected, supplied))


@app.post("/internal/discord/jobs")
async def create_discord_job(request: Request, payload: BotGeneration):
    if not discord_jobs_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    policy_error = check_policy(payload.mode, payload.prompt.strip())
    if policy_error:
        return JSONResponse({"error": policy_error}, status_code=400)
    owner = "discord:" + payload.discord_user_id
    try:
        validate_options(payload.quality, payload.edit_intent, payload.seed)
        existing = JOBS.get(payload.generation_job_id or "", owner) or JOBS.active(owner)
        if existing:
            return JSONResponse({"job": existing.snapshot()}, status_code=202)
        if payload.image_b64 and payload.references:
            raise ReferenceError("Use either references or the legacy image_b64 field")
        submitted = payload.references or ([BotReference(image_b64=payload.image_b64, filename=payload.filename)] if payload.image_b64 else [])
        images = []
        for item in submitted:
            if len(item.image_b64) > 4 * ((MAX_UPLOAD_MB * 1024 * 1024 + 2) // 3):
                raise ReferenceError("Reference image is too large", 413)
            try:
                raw = base64.b64decode(item.image_b64, validate=True)
            except ValueError as exc:
                raise ReferenceError("Invalid reference image encoding", 415) from exc
            images.append(validate_reference(raw))
            validate_reference_set("edit", images)
        resolve_prompt_expansion(payload.prompt_expansion, payload.enhance_prompt, payload.model)
        validate_reference_set(payload.mode, images)
        validate_selected_model(payload.model, payload.mode, images, payload.rapid_steps, payload.quality)
        job, created = JOBS.create(owner, payload.generation_job_id or uuid.uuid4().hex)
        if created:
            async def operation(progress):
                png = await render_image(payload.mode, payload.prompt.strip(), images, quality=payload.quality,
                                         edit_intent=payload.edit_intent, enhance_prompt=payload.enhance_prompt,
                                         seed=payload.seed, progress=progress, model=payload.model, rapid_steps=payload.rapid_steps, prompt_expansion=payload.prompt_expansion)
                progress.update("saving", "Preparing the Discord image")
                with Image.open(io.BytesIO(png)) as image:
                    out = io.BytesIO()
                    image.convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
                return out.getvalue()
            JOBS.launch(job, operation)
        return JSONResponse({"job": job.snapshot()}, status_code=202)
    except (ReferenceError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=getattr(exc, "status_code", 400))


@app.get("/internal/discord/jobs/{job_id}")
async def discord_job_status(request: Request, job_id: str, discord_user_id: str):
    if not discord_jobs_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    job = JOBS.get(job_id, "discord:" + discord_user_id)
    if not job:
        return JSONResponse({"error": "Generation status was lost. The web server may have restarted."}, status_code=404)
    return JSONResponse({"job": job.snapshot()}, headers={"Cache-Control": "no-store"})


@app.get("/internal/discord/jobs/{job_id}/image")
async def discord_job_image(request: Request, job_id: str, discord_user_id: str):
    if not discord_jobs_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    job = JOBS.get(job_id, "discord:" + discord_user_id)
    if not job:
        return JSONResponse({"error": "Image is no longer available."}, status_code=404)
    if job.state != "done" or job.image_bytes is None:
        return JSONResponse({"error": job.error or "Image is not ready yet."}, status_code=409)
    return Response(job.image_bytes, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/internal/discord/generate")
async def discord_generate(request: Request, payload: BotGeneration):
    expected = os.getenv("DISCORD_BOT_SECRET", "")
    authorization = request.headers.get("authorization", "")
    supplied = authorization.removeprefix("Bearer ")
    if not expected or not secrets.compare_digest(expected, supplied):
        return Response("Unauthorized", status_code=401)
    policy_error = check_policy(payload.mode, payload.prompt.strip())
    if policy_error:
        return Response(policy_error, status_code=400)
    try:
        if payload.image_b64 and payload.references:
            raise ReferenceError("Use either references or the legacy image_b64 field")
        submitted = payload.references or ([BotReference(image_b64=payload.image_b64, filename=payload.filename)] if payload.image_b64 else [])
        images = []
        for item in submitted:
            # Reject large encoded bodies before allocating decoded image bytes.
            if len(item.image_b64) > 4 * ((MAX_UPLOAD_MB * 1024 * 1024 + 2) // 3):
                raise ReferenceError("Reference image is too large", 413)
            try:
                raw = base64.b64decode(item.image_b64, validate=True)
            except ValueError as exc:
                raise ReferenceError("Invalid reference image encoding", 415) from exc
            images.append(validate_reference(raw))
            validate_reference_set("edit", images)
        resolve_prompt_expansion(payload.prompt_expansion, payload.enhance_prompt, payload.model)
        validate_reference_set(payload.mode, images)
        validate_selected_model(payload.model, payload.mode, images, payload.rapid_steps, payload.quality)
        png = await render_image(payload.mode, payload.prompt.strip(), images, quality=payload.quality, edit_intent=payload.edit_intent, enhance_prompt=payload.enhance_prompt, seed=payload.seed, model=payload.model, rapid_steps=payload.rapid_steps, prompt_expansion=payload.prompt_expansion)
        with Image.open(io.BytesIO(png)) as image:
            image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=90, optimize=True)
        return Response(out.getvalue(), media_type="image/jpeg", headers={"Content-Disposition": "attachment; filename=generated.jpg"})
    except ReferenceError as exc:
        return Response(str(exc), status_code=exc.status_code)
    except (httpx.HTTPError, KeyError, json.JSONDecodeError, TimeoutError, OSError, RuntimeError) as exc:
        return Response(f"Generation failed: {str(exc)[:1400]}", status_code=502)


@app.get("/image/{image_id}")
async def get_image(request: Request, image_id: str):
    user = current_user(request)
    if not user:
        return Response(status_code=401)
    with db() as conn:
        row = conn.execute("SELECT image_path FROM generations WHERE id=? AND user_id=?", (image_id, user["id"])).fetchone()
    if not row:
        return Response(status_code=404)
    path = Path(row["image_path"])
    if not path.is_file():
        return Response(status_code=404)
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, no-store"})


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, notice: str = "", error: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user["is_admin"]:
        return Response("Forbidden", status_code=403)
    terms = "\n".join(json.loads(setting("blocked_terms", "[]")))
    text_checked = "checked" if setting("allow_text", "true") == "true" else ""
    edit_checked = "checked" if setting("allow_edit", "true") == "true" else ""
    body = f'''<section class="card"><h2>Generation rules</h2><p class="muted">Blocked words and phrases are matched in the submitted prompt, case-insensitively and across punctuation. Put one rule on each line. Generated pixels are not semantically scanned in this initial version; these rules are a prompt filter, not a guarantee about the output.</p><form method="post" action="/admin/policy"><input type="hidden" name="csrf_token" value="{csrf(request)}"><label>Blocked phrases<textarea name="blocked_terms" rows="10" placeholder="e.g. a phrase you want to reject">{html.escape(terms)}</textarea></label><label><input type="checkbox" name="allow_text" value="yes" {text_checked}> Allow new images from text</label><label><input type="checkbox" name="allow_edit" value="yes" {edit_checked}> Allow edits with reference images</label><label>Maximum prompt length<input type="number" name="max_prompt_chars" value="{html.escape(setting("max_prompt_chars", str(MAX_PROMPT_CHARS)))}" min="50" max="{MAX_PROMPT_CHARS}"></label><button>Save rules</button></form></section>
<section class="card"><h2>Add a friend</h2><form method="post" action="/admin/users"><input type="hidden" name="csrf_token" value="{csrf(request)}"><label>Username<input name="username" required minlength="3" maxlength="40"></label><label>Temporary password<input name="password" type="password" required minlength="14"></label><button>Create account</button></form><h3>Accounts</h3>{user_table()}</section>'''
    if error:
        body = body.replace('<section class="card"><h2>Generation rules</h2>', f'<p class="error">{html.escape(error)}</p><section class="card"><h2>Generation rules</h2>')
    return page("Admin", body, user, notice, csrf(request))


def user_table():
    with db() as conn:
        users = conn.execute("SELECT username,is_admin FROM users ORDER BY username").fetchall()
    return '<ul>' + ''.join(f'<li>{html.escape(row["username"])}{" (admin)" if row["is_admin"] else ""}</li>' for row in users) + '</ul>'


@app.post("/admin/policy")
async def save_policy(request: Request, csrf_token: str = Form(...), blocked_terms: str = Form(""), allow_text: str | None = Form(None), allow_edit: str | None = Form(None), max_prompt_chars: int = Form(MAX_PROMPT_CHARS)):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return Response("Forbidden", status_code=403)
    try:
        verify_csrf(request, csrf_token)
    except ValueError:
        return RedirectResponse("/admin?error=Refresh%20the%20page", status_code=303)
    terms = list(dict.fromkeys(line.strip() for line in blocked_terms.splitlines() if line.strip()))[:300]
    max_prompt_chars = min(max(int(max_prompt_chars), 50), MAX_PROMPT_CHARS)
    set_setting("blocked_terms", json.dumps(terms, ensure_ascii=False))
    set_setting("allow_text", "true" if allow_text else "false")
    set_setting("allow_edit", "true" if allow_edit else "false")
    set_setting("max_prompt_chars", str(max_prompt_chars))
    return RedirectResponse("/admin?notice=Rules%20saved", status_code=303)


@app.post("/admin/users")
async def add_user(request: Request, csrf_token: str = Form(...), username: str = Form(...), password: str = Form(...)):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return Response("Forbidden", status_code=403)
    try:
        verify_csrf(request, csrf_token)
    except ValueError:
        return RedirectResponse("/admin?error=Refresh%20the%20page", status_code=303)
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        return RedirectResponse("/admin?error=Username%20must%20be%203-40%20letters%2C%20numbers%2C%20dots%2C%20dashes%2C%20or%20underscores", status_code=303)
    if len(password) < 14:
        return RedirectResponse("/admin?error=Use%20a%20password%20with%20at%20least%2014%20characters", status_code=303)
    try:
        with db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,0)", (username, hash_password(password)))
    except sqlite3.IntegrityError:
        return RedirectResponse("/admin?error=That%20username%20already%20exists", status_code=303)
    return RedirectResponse("/admin?notice=Friend%20account%20created", status_code=303)


@app.get("/internal/generation-status")
async def internal_generation_status(request: Request):
    if not discord_jobs_authorized(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return {"active_jobs": sum(job.state not in {"done", "error"} for job in JOBS.jobs.values())}


install_lora_routes(app, db=db, current_user=current_user, csrf=csrf, verify_csrf=verify_csrf,
                    page=page, comfy_url=COMFY_URL, api_authorized=discord_jobs_authorized)
