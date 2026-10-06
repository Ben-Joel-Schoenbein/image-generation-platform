"""v19/v23 routing, real Discord registration, API wiring and optional download tests."""
import asyncio
import base64
import importlib.util
import io
import json
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for folder in ("web", "discord", "scripts"):
    sys.path.insert(0, str(ROOT / folder))
import rapid_aio as rapid
import heretic_client as heretic
import setup

MODELS = list(rapid.RAPID_MODELS)
V23 = "rapid_aio_v23_nsfw"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def png(width=64, height=48):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def studio(tmp_path, monkeypatch):
    for name, value in {"DATA_DIR": str(tmp_path), "SESSION_SECRET": "s" * 64,
                        "ADMIN_PASSWORD": "private-test-password-12345", "COOKIE_HTTPS_ONLY": "false",
                        "DISCORD_BOT_SECRET": "test-secret", "WORKFLOW_TEXT": str(ROOT / "workflows/text2img.api.json"),
                        "WORKFLOW_EDIT": str(ROOT / "workflows/img2img.api.json")}.items():
        monkeypatch.setenv(name, value)
    return load_module("studio_v23_under_test", ROOT / "web/app.py")


@pytest.fixture
def bot_module(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_BOT_SECRET", "test-secret")
    monkeypatch.setattr(discord.Client, "run", lambda *args, **kwargs: None)
    return load_module("bot_v23_under_test", ROOT / "discord/bot.py")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode,count", [("text", 0), ("edit", 1), ("edit", 4)])
def test_each_checkpoint_routes_to_its_own_loader(model, mode, count):
    uploads = [{"name": f"ref-{i}.png", "subfolder": "batch"} for i in range(count)]
    graph = rapid.build_rapid_workflow(mode, uploads, "test", seed=12345, rapid_steps=4,
                                      source_size=(1200, 800) if count else None, model=model)
    assert graph["1"]["inputs"]["ckpt_name"] == rapid.RAPID_MODELS[model]
    sampler = graph["5"]["inputs"]
    assert (sampler["cfg"], sampler["steps"], sampler["seed"], sampler["sampler_name"], sampler["scheduler"]) == (1.0, 4, 12345, "euler_ancestral", "beta")
    assert all(graph["3"]["inputs"][f"image{i+1}"] == [str(101+i), 0] for i in range(count))
    assert graph["3"]["inputs"]["latent"] == ["2", 0]
    assert graph["2"]["inputs"]["width"] > graph["2"]["inputs"]["height"] if count else graph["2"]["inputs"]["width"] == 1024
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


@pytest.mark.parametrize("model", MODELS)
def test_rapid_reference_and_sampling_limits(model):
    with pytest.raises(ValueError, match="at most 4"):
        rapid.validate_generation_model(model, "edit", 5)
    for steps in (True, 0, 5, 4.0):
        with pytest.raises(ValueError, match="sampling steps"):
            rapid.validate_generation_model(model, "text", 0, steps)
    for steps in (4, 6, 8):
        rapid.validate_generation_model(model, "edit", 4, steps)
    assert heretic.resolve_prompt_expansion(model=model) == "off"
    assert heretic.resolve_prompt_expansion("heretic", model=model) == "heretic"
    assert heretic.resolve_prompt_expansion("standard", model=model) == "standard"
    assert heretic.resolve_prompt_expansion(enhance_prompt=True, model=model) == "standard"


def test_only_requested_nsfw_variant_and_qwen_remain_available():
    assert set(rapid.MODEL_LIMITS) == {"qwen21", "rapid_aio_v19", V23, *rapid.EDIT2511_MODELS}
    rapid.validate_generation_model("qwen21", "edit", 10)
    with pytest.raises(ValueError, match="supported"):
        rapid.validate_generation_model("rapid_aio_v23_sfw", "text", 0)
    with pytest.raises(ValueError, match="Rapid AIO checkpoint"):
        rapid.build_rapid_workflow("text", [], "test", model="qwen21")
    assert rapid.build_rapid_workflow("text", [], "test")["1"]["inputs"]["ckpt_name"] == rapid.MODEL_FILE


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", ["text", "edit"])
def test_standard_expansion_and_prompt_preview_use_the_rapid_encoder(studio, model, mode):
    uploads = [{"name": "ref.png"}] if mode == "edit" else []
    template = studio.build_workflow(json.loads((studio.WORKFLOW_EDIT if uploads else studio.WORKFLOW_TEXT).read_text()), uploads, "original")
    graph = rapid.build_rapid_workflow(mode, uploads, "original", seed=42, enhancer_template=template, model=model)
    heretic.capture_prompt(graph, model)
    assert graph["3"]["inputs"]["prompt"] == ["900", 0]
    assert graph["900"]["inputs"]["text"] == ["12", 0]
    assert graph["10"]["inputs"]["prompt"] == "original"
    assert graph["10"]["inputs"]["sampling_mode.seed"] == 42
    assert graph["5"]["class_type"] == "KSampler"
    if uploads:
        assert graph["11"]["inputs"]["images.image0"] == ["101", 0]


def install_comfy_mock(studio, monkeypatch, lora_names=()):
    graphs, uploads = [], []
    schema = {}
    for path in (studio.WORKFLOW_TEXT, studio.WORKFLOW_EDIT):
        for node in json.loads(path.read_text()).values():
            schema[node["class_type"]] = {"input": {"required": {}}}
    for kind in ("CheckpointLoaderSimple", "EmptySD3LatentImage", "StudioRapidAIOTextEncode", "TextEncodeQwenImageEditPlus", "KSampler", "VAEDecode", "SaveImage", "StudioCapturePrompt", "LoadImage", "ModelSamplingAuraFlow", "CFGNorm"):
        schema[kind] = {"input": {"required": {}}}
    schema["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [list(rapid.RAPID_MODELS.values())]
    schema["LoraLoaderModelOnly"] = {"input": {"required": {"lora_name": [lora_names if isinstance(lora_names, list) else list(lora_names)], "model": ["MODEL"], "strength_model": ["FLOAT"]}}}
    schema["StudioRapidAIOTextEncode"]["input"]["required"] = {name: ["TYPE"] for name in ("clip", "vae", "latent", "prompt")}

    def handle(request):
        if request.url.path == "/object_info":
            return httpx.Response(200, json=schema)
        if request.url.path == "/upload/image":
            uploads.append(request)
            return httpx.Response(200, json={"name": f"ref-{len(uploads)}.png", "subfolder": ""})
        if request.url.path == "/prompt":
            graphs.append(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "test-prompt"})
        if request.url.path == "/view":
            return httpx.Response(200, content=png())
        raise AssertionError(request.url)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(studio.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))

    class Monitor:
        client_id = "test-client"
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def bind(self, *args): pass

    monkeypatch.setattr(studio, "ProgressMonitor", Monitor)
    monkeypatch.setattr(studio, "wait_for_image", AsyncMock(return_value={"filename": "out.png", "subfolder": "", "type": "output"}))
    return graphs, uploads


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", ["text", "edit"])
@pytest.mark.parametrize("expansion", ["off", "standard", "heretic"])
def test_full_render_pipeline_routes_model_and_expansion(studio, monkeypatch, model, mode, expansion):
    studio.initialize()
    graphs, uploaded = install_comfy_mock(studio, monkeypatch)
    rewrite = AsyncMock(return_value="expanded prompt")
    monkeypatch.setattr(studio, "rewrite_prompt", rewrite)
    references = [studio.validate_reference(png(120, 80))] if mode == "edit" else []
    result = asyncio.run(studio.render_image(mode, "original", references, model=model, seed=12345, prompt_expansion=expansion))
    assert result == png()
    graph = graphs[0]
    assert graph["1"]["inputs"]["ckpt_name"] == rapid.RAPID_MODELS[model]
    assert len(uploaded) == len(references)
    assert graph["5"]["inputs"]["seed"] == 12345
    if expansion == "heretic":
        rewrite.assert_awaited_once()
        assert rewrite.call_args.args[0] == mode
        assert rewrite.call_args.args[2] == references
        assert graph["3"]["inputs"]["prompt"] == "expanded prompt"
    else:
        rewrite.assert_not_awaited()
        assert graph["3"]["inputs"]["prompt"] == (["900", 0] if expansion == "standard" else "original")


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
@pytest.mark.parametrize("count", [0, 4])
def test_discord_api_accepts_v23_and_passes_it_to_renderer(studio, monkeypatch, route, count):
    import generation_progress
    monkeypatch.setattr(studio, "JOBS", generation_progress.JobStore())
    renderer = AsyncMock(return_value=png())
    monkeypatch.setattr(studio, "render_image", renderer)
    payload = {"discord_user_id": "1234", "generation_job_id": "a" * 32,
               "mode": "edit" if count else "text", "prompt": "test", "model": V23, "seed": 12,
               "prompt_expansion": "heretic", "references": [{"image_b64": base64.b64encode(png()).decode(), "filename": "ref.png"}] * count}
    with TestClient(studio.app) as client:
        response = client.post(route, json=payload, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == (202 if route.endswith("jobs") else 200)
        if route.endswith("jobs"):
            job_id = response.json()["job"]["id"]
            for _ in range(100):
                status = client.get(f"{route}/{job_id}?discord_user_id=1234", headers={"Authorization": "Bearer test-secret"})
                if status.json()["job"]["state"] == "done": break
            assert status.json()["job"]["state"] == "done"
    assert renderer.call_args.kwargs["model"] == V23
    assert renderer.call_args.kwargs["prompt_expansion"] == "heretic"
    assert len(renderer.call_args.args[2]) == count


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
def test_discord_api_rejects_fifth_v23_reference_before_render(studio, monkeypatch, route):
    renderer = AsyncMock()
    monkeypatch.setattr(studio, "render_image", renderer)
    payload = {"discord_user_id": "1234", "mode": "edit", "prompt": "test", "model": V23,
               "references": [{"image_b64": base64.b64encode(png()).decode(), "filename": "ref.png"}] * 5}
    with TestClient(studio.app) as client:
        response = client.post(route, json=payload, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 400
        assert "at most 4" in response.text
    renderer.assert_not_awaited()


def test_missing_v23_checkpoint_reports_exact_path(studio):
    graph = rapid.build_rapid_workflow("text", [], "test", model=V23)
    schema = {node["class_type"]: {"input": {"required": {}}} for node in graph.values()}
    schema["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [[rapid.MODEL_FILE]]
    with pytest.raises(RuntimeError, match="models/checkpoints/Qwen-Rapid-AIO-NSFW-v23.safetensors"):
        studio.validate_workflow(graph, schema)


@pytest.mark.parametrize("mode", ["text", "edit"])
def test_website_form_passes_v23_to_its_checkpoint_and_saves_image(studio, monkeypatch, mode):
    import re
    graphs, uploaded = install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        login_page = client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)[1]
        home = client.post("/login", data={"username": "admin", "password": "private-test-password-12345", "csrf_token": token})
        assert home.status_code == 200
        assert 'value="rapid_aio_v23_nsfw"' in home.text
        token = re.search(r'name="csrf_token" value="([^"]+)"', home.text)[1]
        response = client.post("/generate", data={"mode": mode, "prompt": "test", "model": V23,
                               "csrf_token": token, "seed": "12345", "prompt_expansion": "off"},
                               files=[("references", ("reference.png", png(), "image/png"))] if mode == "edit" else None)
        assert response.status_code == 200
        assert "Image ready" in response.text
        assert graphs[0]["1"]["inputs"]["ckpt_name"] == rapid.RAPID_MODELS[V23]
        assert len(uploaded) == (1 if mode == "edit" else 0)
        assert len(list(studio.DATA_DIR.glob("images/*/*.png"))) == 1


def test_real_discord_command_registration_includes_v23(bot_module):
    assert bot_module.RAPID_MODELS == set(rapid.RAPID_MODELS)
    for name in ("imagine", "edit"):
        command = bot_module.bot.tree.get_command(name)
        models = next(parameter for parameter in command.parameters if parameter.name == "model")
        assert {choice.value for choice in models.choices} == {"qwen21", *MODELS, *rapid.EDIT2511_MODELS}
        assert len(command.parameters) <= 25
        assert {"rapid_steps", "prompt_expansion"}.issubset({p.name for p in command.parameters})
    assert len([p for p in bot_module.bot.tree.get_command("edit").parameters if p.type == discord.AppCommandOptionType.attachment]) == 10


@pytest.mark.parametrize("model,count", [(V23, 4), ("rapid_aio_v19", 4), ("qwen21", 10)])
def test_discord_bot_submits_model_and_all_permitted_references(bot_module, monkeypatch, model, count):
    class Attachment:
        size = 4
        content_type = "image/png"
        filename = "ref.png"
        async def read(self, **kwargs): return b"test"
    interaction = SimpleNamespace(guild_id=123, user=SimpleNamespace(id=456),
                                  response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda _: object())
    deliver = AsyncMock()
    monkeypatch.setattr(bot_module, "deliver_generation", deliver)
    asyncio.run(bot_module.generate(interaction, "edit", "test", [Attachment() for _ in range(count)], model=model, prompt_expansion="heretic", rapid_steps=6))
    assert len(deliver.call_args.args[4]["references"]) == count
    assert deliver.call_args.args[4]["model"] == model
    assert deliver.call_args.args[4]["prompt_expansion"] == "heretic"


def test_discord_bot_rejects_fifth_v23_reference_before_download(bot_module, monkeypatch):
    interaction = SimpleNamespace(guild_id=123, response=SimpleNamespace(send_message=AsyncMock()))
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda _: object())
    asyncio.run(bot_module.generate(interaction, "edit", "test", [object()] * 5, model=V23))
    assert "at most 4" in interaction.response.send_message.call_args.args[0]


def test_website_controls_apply_rapid_limits_and_keep_prompt_preferences():
    markup = rapid.rapid_controls()
    assert 'value="rapid_aio_v23_nsfw"' in markup
    script = markup.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = '''
const assert = require('node:assert/strict');
const model = {value:'qwen21',addEventListener:(event,fn)=>model.change=fn};
const label = {firstChild:{nodeType:3,textContent:''}};
const picker = {files:Array(5).fill({}),setCustomValidity:msg=>picker.error=msg,closest:()=>label,addEventListener:(event,fn)=>picker.change=fn};
const enhancer = {tagName:'SELECT',value:'standard'};
const steps = {}, stepLabel = {}, help = {};
const form = {elements:{model,references:picker,prompt_expansion:enhancer,rapid_steps:steps}};
const elements = {'generation-form':form,'rapid-steps-label':stepLabel,'rapid-model-help':help};
global.document = {getElementById:id=>elements[id],addEventListener:(event,fn)=>fn()};
''' + script + '''
assert.equal(steps.disabled,true);assert.equal(picker.error,'');
model.value='rapid_aio_v23_nsfw';model.change();
assert.equal(steps.disabled,false);assert.equal(stepLabel.hidden,false);assert.match(picker.error,/at most 4/);assert.equal(enhancer.value,'off');
enhancer.value='heretic';model.value='rapid_aio_v19';model.change();assert.equal(enhancer.value,'off');
model.value='rapid_aio_v23_nsfw';model.change();assert.equal(enhancer.value,'heretic');
picker.files=Array(4).fill({});picker.change();assert.equal(picker.error,'');
model.value='qwen21';model.change();assert.equal(steps.disabled,true);assert.equal(enhancer.value,'standard');assert.match(label.firstChild.textContent,/1–10/);
'''
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_setup_defaults_retain_old_model_set_and_v23_is_opt_in():
    manifest = setup.load_manifest()
    baseline = setup.selected_manifest(manifest)
    with_v23 = setup.selected_manifest(manifest, True)
    single = setup.rapid_v23_manifest(manifest)
    assert len(baseline["files"]) == 13
    assert sum(item["size"] for item in baseline["files"]) == 92518531074
    assert len(with_v23["files"]) == 14
    assert len(single["files"]) == 1
    assert single["files"][0]["destination"] == "models/checkpoints/Qwen-Rapid-AIO-NSFW-v23.safetensors"
    assert single["files"][0]["size"] == 28431840023
    assert single["files"][0]["sha256"] == "fdb919fc81bea63f13759967fc92c9118142e5c70d4e6795199233a35eefa233"
    setup.project_check(ROOT, manifest)


def test_project_check_refuses_missing_optional_variant():
    with pytest.raises(ValueError, match="variant differs"):
        setup.project_check(ROOT, setup.selected_manifest(setup.load_manifest()))


def test_download_only_script_uses_one_checkpoint_without_configuration_changes(monkeypatch):
    downloader = load_module("v23_downloader_under_test", ROOT / "scripts/download-rapid-v23.py")
    calls = []
    monkeypatch.setattr(sys, "argv", ["download-rapid-v23.py", "--project", str(ROOT)])
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/curl")
    monkeypatch.setattr(downloader.setup, "download_files", lambda root, manifest: calls.append((root, manifest)))
    downloader.main()
    assert len(calls[0][1]["files"]) == 1
    assert calls[0][1]["files"][0]["model"] == V23


def test_smoke_script_compares_both_models_and_saves_parameters(tmp_path, monkeypatch):
    smoke = load_module("v23_smoke_under_test", ROOT / "scripts/test-rapid-v23.py")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_SECRET", "test-secret")
    monkeypatch.setattr(sys, "argv", ["test-rapid-v23.py", "--compare-v19"])
    payloads = []
    def api(url, secret, payload=None):
        assert secret == "test-secret"
        if url.endswith("generation-status"): return {"active_jobs": 0}
        if payload:
            payloads.append(payload)
            return {"job": {"id": payload["generation_job_id"], "state": "done", "label": "Image ready"}}
        assert "/image?" in url
        return b"jpeg-image"
    monkeypatch.setattr(smoke, "api_request", api)
    smoke.main()
    assert [p["model"] for p in payloads] == ["rapid_aio_v19", V23]
    assert payloads[0]["seed"] == payloads[1]["seed"] == 12345
    assert len(list(tmp_path.rglob("*.jpg"))) == 2
    assert len(list(tmp_path.rglob("*.json"))) == 2


def test_smoke_script_reports_busy_worker_before_submitting(monkeypatch):
    smoke = load_module("v23_smoke_busy_test", ROOT / "scripts/test-rapid-v23.py")
    monkeypatch.setenv("DISCORD_BOT_SECRET", "test-secret")
    monkeypatch.setattr(sys, "argv", ["test-rapid-v23.py"])
    monkeypatch.setattr(smoke, "api_request", lambda *args: {"active_jobs": 1})
    with pytest.raises(RuntimeError, match="busy"):
        smoke.main()
