import asyncio
import base64
import importlib.util
import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def png_bytes(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-with-at-least-32-characters")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-password-with-14-characters")
    monkeypatch.setenv("COOKIE_HTTPS_ONLY", "false")
    monkeypatch.setenv("DISCORD_BOT_SECRET", "test-bot-secret")
    monkeypatch.setenv("WORKFLOW_TEXT", str(ROOT / "workflows/text2img.api.json"))
    monkeypatch.setenv("WORKFLOW_EDIT", str(ROOT / "workflows/img2img.api.json"))
    spec = importlib.util.spec_from_file_location("studio_under_test", ROOT / "web/app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def schema_for(workflow):
    schema = {}
    for node in workflow.values():
        schema[node["class_type"]] = {"input": {"required": {}}, "output": ["OUTPUT", "OUTPUT"]}
    for node in workflow.values():
        for name in ("ckpt_name", "unet_name", "clip_name", "vae_name", "lora_name"):
            value = node["inputs"].get(name)
            if isinstance(value, str):
                options = schema[node["class_type"]]["input"]["required"].setdefault(name, [[]])[0]
                if value not in options:
                    options.append(value)
    schema["LoraLoaderModelOnly"] = {"input": {"required": {"lora_name": [[]]}}}
    schema["StudioCapturePrompt"] = {"input": {"required": {"text": ["STRING"]}}}
    return schema


@pytest.mark.parametrize("count", [1, 3, 10])
def test_workflow_keeps_every_reference_and_prunes_unused_inputs(studio, count):
    template = json.loads(studio.WORKFLOW_EDIT.read_text())
    uploads = [{"name": f"reference-{i}.png", "subfolder": "batch"} for i in range(count)]
    workflow = studio.build_workflow(template, uploads, "combine all images")
    loads = [node for node in workflow.values() if node["class_type"] == "LoadImage"]
    assert len(loads) == count
    assert {node["inputs"]["image"] for node in loads} == {f"batch/reference-{i}.png" for i in range(count)}
    reference_inputs = [key for key in workflow["5"]["inputs"] if key.startswith("image")]
    assert len(reference_inputs) == count
    assert isinstance(workflow["6"]["inputs"]["seed"], int)
    studio.validate_workflow(workflow, schema_for(workflow))
    for node in workflow.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in workflow


def test_missing_qwen21_reports_exact_model_path(studio):
    workflow = studio.build_workflow(json.loads(studio.WORKFLOW_TEXT.read_text()), [], "a landscape")
    schema = schema_for(workflow)
    schema["UNETLoader"]["input"]["required"]["unet_name"] = [[]]
    with pytest.raises(RuntimeError, match="models/diffusion_models/qwen_image_2.1_bf16.safetensors"):
        studio.validate_workflow(workflow, schema)


def test_missing_custom_node_and_comfy_400_details(studio):
    with pytest.raises(RuntimeError, match="StudioQwenImageEditReferences is missing"):
        studio.validate_workflow({"6": {"class_type": "StudioQwenImageEditReferences", "inputs": {}}}, {})
    error = httpx.Response(400, json={"error": {"message": "Prompt outputs failed validation"},
        "node_errors": {"1": {"errors": [{"message": "Value not in list", "details": "ckpt_name: missing.safetensors"}]}}})
    assert "ckpt_name: missing.safetensors" in studio.comfy_error(error)


def install_comfy_mock(studio, monkeypatch, reject=False):
    calls = []
    text = json.loads(studio.WORKFLOW_TEXT.read_text())
    edit = json.loads(studio.WORKFLOW_EDIT.read_text())
    schema = schema_for(text) | schema_for(edit)
    class Monitor:
        client_id = "test-client"
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def bind(self, *args): pass
    monkeypatch.setattr(studio, "ProgressMonitor", Monitor)

    def handle(request):
        calls.append(request)
        if request.url.path == "/object_info":
            return httpx.Response(200, json=schema)
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": f"upload-{len(calls)}.png", "subfolder": "", "type": "input"})
        if request.url.path == "/prompt":
            if reject:
                return httpx.Response(400, json={"error": {"message": "Bad workflow"},
                    "node_errors": {"12": {"errors": [{"message": "Invalid sampler", "details": "sampler_name unsupported"}]}}})
            return httpx.Response(200, json={"prompt_id": "test-prompt"})
        if request.url.path == "/view":
            return httpx.Response(200, content=png_bytes())
        raise AssertionError(request.url)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(studio.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))

    async def ready(*args, **kwargs):
        return {"filename": "output.png", "subfolder": "", "type": "output"}

    monkeypatch.setattr(studio, "wait_for_image", ready)
    return calls


def login(client):
    import re
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post("/login", data={"username": "admin", "password": "test-password-with-14-characters", "csrf_token": token})
    assert response.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def test_website_ten_files_reach_comfy_and_image_is_saved(studio, monkeypatch):
    calls = install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        token = login(client)
        response = client.post("/generate", data={"mode": "edit", "prompt": "combine these", "csrf_token": token},
            files=[("references", (f"image-{i}.png", png_bytes(), "image/png")) for i in range(10)])
        assert response.status_code == 200
        assert "Image ready" in response.text
        assert len([call for call in calls if call.url.path == "/upload/image"]) == 10
        graph = json.loads(next(call for call in calls if call.url.path == "/prompt").content)["prompt"]
        assert graph["5"]["inputs"]["images.image_10"] == ["110", 0]
        assert len(list(studio.DATA_DIR.glob("images/*/*.png"))) == 1


def test_website_rejects_eleven_files_before_comfy(studio, monkeypatch):
    calls = install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        token = login(client)
        response = client.post("/generate", data={"mode": "edit", "prompt": "combine", "csrf_token": token},
            files=[("references", (f"{i}.png", png_bytes(), "image/png")) for i in range(11)])
        assert "at most 10" in response.text
        assert calls == []


def test_website_shows_comfy_validation_reason(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch, reject=True)
    with TestClient(studio.app) as client:
        token = login(client)
        response = client.post("/generate", data={"mode": "text", "prompt": "landscape", "csrf_token": token})
        assert "sampler_name unsupported" in response.text


@pytest.mark.parametrize("count", [1, 10])
def test_bot_api_ten_references_and_legacy_payload(studio, monkeypatch, count):
    calls = install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        image = base64.b64encode(png_bytes()).decode()
        payload = {"discord_user_id": "123", "mode": "edit", "prompt": "combine"}
        if count == 1:
            payload["image_b64"] = image
        else:
            payload["references"] = [{"image_b64": image, "filename": f"{i}.png"} for i in range(count)]
        response = client.post("/internal/discord/generate", headers={"Authorization": "Bearer test-bot-secret"}, json=payload)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert len([call for call in calls if call.url.path == "/upload/image"]) == count


def test_api_auth_policy_invalid_images_and_limits(studio, monkeypatch):
    calls = install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        endpoint = "/internal/discord/generate"
        payload = {"discord_user_id": "123", "mode": "edit", "prompt": "combine"}
        headers = {"Authorization": "Bearer test-bot-secret"}
        assert client.post(endpoint, json=payload).status_code == 401
        assert client.post(endpoint, json=payload, headers=headers).status_code == 400
        payload["references"] = [{"image_b64": "not-an-image"}]
        assert client.post(endpoint, json=payload, headers=headers).status_code == 415
        payload["references"] = [{"image_b64": base64.b64encode(b"invalid bytes").decode()}]
        assert client.post(endpoint, json=payload, headers=headers).status_code == 415
        payload["references"] = [{"image_b64": base64.b64encode(png_bytes()).decode()}] * 11
        assert client.post(endpoint, json=payload, headers=headers).status_code == 422
        studio.set_setting("blocked_terms", '["blocked phrase"]')
        payload["references"] = []
        payload["prompt"] = "blocked phrase"
        assert client.post(endpoint, json=payload, headers=headers).status_code == 400
        assert calls == []
    monkeypatch.setattr(studio, "MAX_UPLOAD_MB", 0)
    with pytest.raises(studio.ReferenceError) as oversized:
        studio.validate_reference(png_bytes())
    assert oversized.value.status_code == 413
    monkeypatch.setattr(studio, "MAX_REFERENCE_TOTAL_MB", 0)
    with pytest.raises(studio.ReferenceError, match="together"):
        studio.validate_reference_set("edit", [studio.ReferenceImage(png_bytes(), "test.png", "image/png")])
