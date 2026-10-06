"""Automatically discover LoRAs, with strengths and per-model assignments."""
import hashlib
import html
import json
import math
from pathlib import Path, PurePosixPath
from urllib.parse import quote

CONFIG_KEY = "rapid_aio_loras_v1"
DEFAULT_CONFIG = {"version": 1, "enabled": True, "default_strength": 0.6, "overrides": {}}
DEFAULTS_PATH = Path(__file__).with_name("lora_defaults.json")
TARGETS = {"qwen_edit", "qwen21"}
MODEL_LABELS = {
    "qwen21": "Qwen Image 2.1",
    "rapid_aio_v19": "Qwen Rapid AIO v19 — NSFW",
    "rapid_aio_v23_nsfw": "Qwen Rapid AIO v23 — NSFW",
    "qwen_edit_2511_fp8": "Qwen Image Edit 2511 — FP8 Mixed",
    "qwen_edit_2511_bf16": "Qwen Image Edit 2511 — BF16",
}
FAMILY_MODELS = {
    "qwen21": ["qwen21"],
    "qwen_edit": [model for model in MODEL_LABELS if model != "qwen21"],
}
MODEL_FORM_VERSION = "per_model_v1"


def default_target(name):
    # Retain the existing AIO behavior for files in the root/other subfolders.
    return "qwen21" if name.split("/", 1)[0].casefold() in {"qwen21", "qwen2.1"} else "qwen_edit"


def target(value):
    if not isinstance(value, str) or value not in TARGETS:
        raise ValueError("Choose Qwen 2.1 or Edit 2511 / Rapid AIO for each LoRA")
    return value


def model_selection(values):
    if not isinstance(values, list) or any(not isinstance(value, str) or value not in MODEL_LABELS for value in values):
        raise ValueError("Choose supported image models for each LoRA")
    if len(set(values)) != len(values):
        raise ValueError("Each model may be selected only once for a LoRA")
    # Canonical order makes settings revisions independent of checkbox order.
    return [model for model in MODEL_LABELS if model in values]


def assigned_models(name, item):
    if "models" in item:
        return model_selection(item["models"])
    # Older configurations keep their behavior until explicitly edited.
    return list(FAMILY_MODELS[target(item.get("target", default_target(name)))])


def filename(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("Invalid LoRA filename")
    parts = PurePosixPath(value)
    if not parts.parts or parts.is_absolute() or ".." in parts.parts or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid LoRA filename")
    return value


def strength(value):
    if isinstance(value, bool):
        raise ValueError("LoRA strength must be a number between -2 and 2")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("LoRA strength must be a number between -2 and 2")
    if not math.isfinite(result) or not -2 <= result <= 2:
        raise ValueError("LoRA strength must be a number between -2 and 2")
    return result


def parse_config(raw=None):
    if raw is None:
        raw = DEFAULTS_PATH.read_text() if DEFAULTS_PATH.is_file() else json.dumps(DEFAULT_CONFIG)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid LoRA settings; restore the configuration") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1 or not isinstance(data.get("enabled"), bool) or not isinstance(data.get("overrides"), dict):
        raise ValueError("Invalid LoRA settings")
    result = {"version": 1, "enabled": data["enabled"], "default_strength": strength(data.get("default_strength")), "overrides": {}}
    for name, item in data["overrides"].items():
        filename(name)
        if not isinstance(item, dict) or not isinstance(item.get("enabled"), bool):
            raise ValueError("Invalid settings for LoRA " + name)
        result["overrides"][name] = {"enabled": item["enabled"], "strength": strength(item.get("strength"))}
        if "target" in item:
            result["overrides"][name]["target"] = target(item["target"])
        if "models" in item:
            result["overrides"][name]["models"] = model_selection(item["models"])
    return result


def config_token(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def read_config(db):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (CONFIG_KEY,)).fetchone()
    return parse_config(row["value"] if row else None)


def catalog_from_schema(schema):
    try:
        values = schema["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
    except (KeyError, TypeError, IndexError) as exc:
        raise RuntimeError("ComfyUI's LoRA loader is unavailable; rebuild ComfyUI or disable automatic LoRAs") from exc
    if not isinstance(values, list):
        raise RuntimeError("ComfyUI returned an invalid LoRA file list")
    return sorted({filename(value) for value in values}, key=lambda value: (value.casefold(), value))


def catalog_items(names, config):
    result = []
    for name in names:
        item = config["overrides"].get(name, {"enabled": True, "strength": config["default_strength"]})
        models = assigned_models(name, item)
        family = "qwen21" if models == ["qwen21"] else "mixed" if "qwen21" in models else "qwen_edit"
        result.append({"name": name, "enabled": item["enabled"], "strength": item["strength"],
                       "active": config["enabled"] and item["enabled"] and item["strength"] != 0 and bool(models),
                       "custom": name in config["overrides"], "target": family, "models": models})
    return result


def selected_loras(schema, config, model="rapid_aio_v19"):
    if model not in MODEL_LABELS:
        raise ValueError("Choose a supported image model")
    if not config["enabled"]:
        return []
    return [item for item in catalog_items(catalog_from_schema(schema), config)
            if item["active"] and model in item["models"]]


def apply_loras(graph, items, model_node="5"):
    # Validate all values before altering the graph. Zero disables a LoRA.
    checked = [{"name": filename(item["name"]), "strength": strength(item["strength"])} for item in (items or [])]
    if len({item["name"] for item in checked}) != len(checked):
        raise ValueError("Each LoRA may be applied only once")
    model_input = graph[model_node]["inputs"]["model"]
    node_index = 2000
    for item in checked:
        if item["strength"] == 0:
            continue
        while str(node_index) in graph:
            node_index += 1
        node_id = str(node_index)
        graph[node_id] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model_input, "lora_name": item["name"], "strength_model": item["strength"]}}
        model_input = [node_id, 0]
        node_index += 1
    graph[model_node]["inputs"]["model"] = model_input
    return graph


async def fetch_catalog(comfy_url):
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(comfy_url + "/object_info")
        response.raise_for_status()
        return catalog_from_schema(response.json())


def settings_markup(names, config, csrf_token, error=""):
    escape = html.escape
    rows = []
    for index, item in enumerate(catalog_items(names, config)):
        name = escape(item["name"], quote=True)
        checked = "checked" if item["enabled"] else ""
        choices = ''.join(f'<label><input type="checkbox" name="models_{index}" value="{value}" '
                          f'{"checked" if value in item["models"] else ""}> {label}</label>'
                          for value, label in MODEL_LABELS.items())
        rows.append(f'''<fieldset style="border:1px solid #46506a;border-radius:8px;margin:12px 0;padding:12px">
<legend style="overflow-wrap:anywhere">{name}</legend><input type="hidden" name="filename" value="{name}">
<label><input type="checkbox" name="active" value="{name}" {checked}> Enabled</label>
<fieldset style="border:0;padding:0;margin:12px 0"><legend>Apply to models</legend>{choices}</fieldset>
<label>Strength<input type="number" name="strength" value="{item['strength']:g}" min="-2" max="2" step="any" required></label></fieldset>''')
    missing = sorted(set(config["overrides"]) - set(names))
    missing_html = "<p class=muted>Not currently found; saved strengths are retained:</p><ul>" + "".join(f"<li>{escape(name)}</li>" for name in missing) + "</ul>" if missing else ""
    enabled = "checked" if config["enabled"] else ""
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return f'''<section class="card"><h2>Qwen LoRAs</h2>{error_html}
<p>Files in models/loras, including subfolders, are detected automatically. Select exactly which models may use each LoRA below. Existing strengths and family assignments are retained. New files in models/loras/qwen21 default to Qwen Image 2.1; other new files default to all Edit 2511 / Rapid AIO models. Website and Discord share these settings.</p>
<p class="muted">Choose models the LoRA is compatible with; assignment does not verify compatibility. No selected models, strength 0 or disabling a file prevents its use. Acceleration LoRAs may need their own sampler, step count or schedule. Settings take effect on the next job.</p>
<p><a href="/admin/loras">Refresh file list</a> · <a href="/admin">Other admin settings</a></p>
<form method="post" action="/admin/loras"><input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
<input type="hidden" name="revision" value="{config_token(config)}">
<input type="hidden" name="model_selection" value="{MODEL_FORM_VERSION}">
<label><input type="checkbox" name="auto_enabled" value="yes" {enabled}> Enable automatic LoRAs</label>
<label>Default strength for new files<input type="number" name="default_strength" value="{config['default_strength']:g}" min="-2" max="2" step="any" required></label>
{''.join(rows) or '<p class="muted">No LoRA files found.</p>'}{missing_html}
<button>Save LoRA settings</button></form></section>'''


def install_lora_routes(app, *, db, current_user, csrf, verify_csrf, page, comfy_url, api_authorized):
    import httpx
    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

    @app.get("/admin/loras", response_class=HTMLResponse)
    async def lora_admin(request: Request, notice: str = "", error: str = ""):
        user = current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not user["is_admin"]:
            return Response("Forbidden", status_code=403)
        try:
            config = read_config(db)
            try:
                names = await fetch_catalog(comfy_url)
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                # Keep controls usable so an unavailable loader can be disabled.
                names, error = [], "LoRA file list unavailable: " + str(exc)[:500]
            return page("LoRAs", settings_markup(names, config, csrf(request), error), user, notice, csrf(request))
        except ValueError as exc:
            return page("LoRAs", '<p class="error">' + html.escape(str(exc)) + "</p>", user)

    @app.post("/admin/loras")
    async def save_loras(request: Request):
        user = current_user(request)
        if not user or not user["is_admin"]:
            return Response("Forbidden", status_code=403)
        try:
            form = await request.form(max_fields=10000)
            verify_csrf(request, form.get("csrf_token", ""))
            names, weights, active = form.getlist("filename"), form.getlist("strength"), set(form.getlist("active"))
            targets = form.getlist("target")
            per_model = form.getlist("model_selection") == [MODEL_FORM_VERSION]
            model_fields = {key for key in form if key.startswith("models_")}
            expected_fields = {f"models_{index}" for index in range(len(names))}
            if ("model_selection" in form and not per_model) or (model_fields and not per_model) or not model_fields.issubset(expected_fields) or (per_model and targets):
                raise ValueError("Invalid LoRA model selection; refresh before saving")
            if len(names) != len(weights) or (targets and len(targets) != len(names)) or len(set(names)) != len(names) or not active.issubset(set(names)):
                raise ValueError("Invalid LoRA form; refresh before saving")
            updates = {filename(name): {"enabled": name in active, "strength": strength(weight)} for name, weight in zip(names, weights)}
            if per_model:
                for index, name in enumerate(names):
                    updates[name]["models"] = model_selection(form.getlist(f"models_{index}"))
            elif targets:
                for name, family in zip(names, targets):
                    updates[name]["target"] = target(family)
            default_strength = strength(form.get("default_strength"))
            with db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT value FROM settings WHERE key=?", (CONFIG_KEY,)).fetchone()
                config = parse_config(row["value"] if row else None)
                if form.get("revision") != config_token(config):
                    raise ValueError("LoRA settings changed; refresh the page before saving")
                config["enabled"] = form.get("auto_enabled") == "yes"
                config["default_strength"] = default_strength
                if not per_model and not targets:
                    for name, item in updates.items():
                        previous = config["overrides"].get(name, {})
                        for key in ("target", "models"):
                            if key in previous:
                                item[key] = previous[key]
                config["overrides"].update(updates)
                conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CONFIG_KEY, json.dumps(config)))
            return RedirectResponse("/admin/loras?notice=LoRA%20settings%20saved", status_code=303)
        except (ValueError, TypeError) as exc:
            return RedirectResponse("/admin/loras?error=" + quote(str(exc)), status_code=303)

    @app.get("/internal/discord/loras")
    async def discord_loras(request: Request):
        if not api_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            config = read_config(db)
            names = await fetch_catalog(comfy_url)
            return JSONResponse({"enabled": config["enabled"], "default_strength": config["default_strength"],
                                 "items": catalog_items(names, config)}, headers={"Cache-Control": "no-store"})
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            return JSONResponse({"error": "LoRA discovery failed: " + str(exc)[:500]}, status_code=502)
