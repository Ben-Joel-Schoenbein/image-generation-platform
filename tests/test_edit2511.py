"""Separate precision loaders, real routes, prompt expansion and LoRA families."""
import asyncio
import base64
import copy
import json
import re
import sys
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from test_rapid_v23 import ROOT, studio, bot_module, install_comfy_mock, png, rapid, heretic, setup, load_module
import lora_support as lora

MODELS = list(rapid.EDIT2511_MODELS)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode,count", [("text", 0), ("edit", 1), ("edit", 3)])
@pytest.mark.parametrize("steps", [20, 40])
def test_edit_workflow_uses_separate_components_and_native_sampling(model, mode, count, steps):
    uploads = [{"name": f"ref{i}.png"} for i in range(count)]
    graph = rapid.build_edit2511_workflow(mode, uploads, "test", seed=42, edit_steps=steps,
                                        model=model, source_size=(1200, 800) if count else None)
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["1"]["inputs"] == {"unet_name": rapid.EDIT2511_MODELS[model], "weight_dtype": "default"}
    assert graph["20"]["inputs"]["clip_name"] == rapid.EDIT2511_TEXT_ENCODER
    assert graph["21"]["inputs"]["vae_name"] == rapid.EDIT2511_VAE
    assert graph["3"]["inputs"]["clip"] == graph["4"]["inputs"]["clip"] == ["20", 0]
    assert graph["3"]["inputs"]["vae"] == graph["6"]["inputs"]["vae"] == ["21", 0]
    assert graph["22"]["inputs"]["shift"] == 3.1
    assert graph["23"]["class_type"] == "CFGNorm"
    sampler = graph["5"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (steps, 4, "euler", "simple")
    assert sampler["seed"] == 42
    for i in range(1, count+1):
        assert graph["3"]["inputs"][f"image{i}"] == graph["4"]["inputs"][f"image{i}"]
    assert not any(node["class_type"] == "CheckpointLoaderSimple" for node in graph.values())
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("steps", [True, 4, 0, 40.0, 41])
def test_edit_sampling_is_validated_independently_of_rapid(model, steps):
    with pytest.raises(ValueError, match="sampling steps"):
        rapid.validate_generation_model(model, "text", 0, edit_steps=steps)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", ["text", "edit"])
@pytest.mark.parametrize("expansion", ["off", "standard", "heretic"])
def test_render_expansion_and_loras_keep_the_selected_precision(studio, monkeypatch, model, mode, expansion):
    studio.initialize()
    graphs, uploaded = install_comfy_mock(studio, monkeypatch, ["edit.safetensors", "qwen21/style.safetensors"])
    rewrite = AsyncMock(return_value="expanded")
    monkeypatch.setattr(studio, "rewrite_prompt", rewrite)
    references = [studio.validate_reference(png(120, 80))] if mode == "edit" else []
    result = asyncio.run(studio.render_image(mode, "original", references, model=model,
                                           seed=42, edit_steps=30, prompt_expansion=expansion))
    assert result == png()
    graph = graphs[0]
    assert graph["1"]["inputs"]["unet_name"] == rapid.EDIT2511_MODELS[model]
    assert graph["5"]["inputs"]["steps"] == 30
    assert graph["2000"]["inputs"]["lora_name"] == "edit.safetensors"
    assert "2001" not in graph
    assert len(uploaded) == len(references)
    assert graph["4"]["inputs"]["prompt"] == ""
    if expansion == "standard":
        assert graph["3"]["inputs"]["prompt"] == ["900", 0]
        assert graph["900"]["inputs"]["text"] == ["12", 0]
        assert graph["10"]["inputs"]["prompt"] == "original"
        rewrite.assert_not_awaited()
    elif expansion == "heretic":
        rewrite.assert_awaited_once()
        assert graph["3"]["inputs"]["prompt"] == "expanded"
    else:
        assert graph["3"]["inputs"]["prompt"] == "original"


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
def test_discord_api_passes_edit_steps_and_model_to_the_renderer(studio, monkeypatch, model, route):
    import generation_progress
    monkeypatch.setattr(studio, "JOBS", generation_progress.JobStore())
    renderer = AsyncMock(return_value=png())
    monkeypatch.setattr(studio, "render_image", renderer)
    payload = {"discord_user_id": "test", "generation_job_id": "c"*32, "mode": "text",
               "prompt": "test", "model": model, "edit_steps": 20, "prompt_expansion": "standard"}
    with TestClient(studio.app) as client:
        response = client.post(route, json=payload, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == (202 if route.endswith("jobs") else 200), response.text
        if route.endswith("jobs"):
            for _ in range(100):
                status = client.get(f"{route}/{response.json()['job']['id']}?discord_user_id=test",
                                    headers={"Authorization": "Bearer test-secret"}).json()["job"]
                if status["state"] == "done": break
            assert status["state"] == "done"
    assert renderer.call_args.kwargs["edit_steps"] == 20
    assert renderer.call_args.kwargs["model"] == model


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
@pytest.mark.parametrize("invalid", ["references", "edit_steps", "boolean"])
def test_edit_api_rejects_invalid_input_before_render(studio, monkeypatch, route, invalid):
    renderer = AsyncMock()
    monkeypatch.setattr(studio, "render_image", renderer)
    payload = {"discord_user_id": "test", "mode": "text", "prompt": "test", "model": MODELS[0]}
    if invalid == "references":
        payload.update(mode="edit", references=[{"image_b64": base64.b64encode(png()).decode()}]*4)
    else:
        payload["edit_steps"] = True if invalid == "boolean" else 4
    with TestClient(studio.app) as client:
        response = client.post(route, json=payload, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == (422 if invalid == "boolean" else 400)
    renderer.assert_not_awaited()


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("route", ["/generate", "/generation-jobs"])
def test_website_forms_forward_steps_and_selected_precision(studio, monkeypatch, model, route):
    import generation_progress
    store = generation_progress.JobStore()
    monkeypatch.setattr(studio, "JOBS", store)
    monkeypatch.setattr(generation_progress, "JOBS", store)
    renderer = AsyncMock(return_value=png())
    monkeypatch.setattr(studio, "render_image", renderer)
    with TestClient(studio.app) as client:
        token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text)[1]
        home = client.post("/login", data={"username": "admin", "password": "private-test-password-12345", "csrf_token": token})
        assert all(f'value="{m}"' in home.text for m in MODELS)
        token = re.search(r'name="csrf_token" value="([^"]+)"', home.text)[1]
        response = client.post(route, data={"csrf_token": token, "mode": "text", "prompt": "test",
                                           "model": model, "edit_steps": "30", "prompt_expansion": "heretic", "generation_job_id": "d"*32})
        assert response.status_code == (202 if route == "/generation-jobs" else 200), response.text
        if route == "/generation-jobs":
            for _ in range(100):
                state = client.get(f"/generation-jobs/{response.json()['job']['id']}").json()["job"]["state"]
                if state == "done": break
            assert state == "done"
    assert renderer.call_args.kwargs["model"] == model
    assert renderer.call_args.kwargs["edit_steps"] == 30


def test_discord_commands_register_edit_models_and_independent_steps(bot_module):
    for name in ("imagine", "edit"):
        command = bot_module.bot.tree.get_command(name)
        fields = {p.name: p for p in command.parameters}
        assert {c.value for c in fields["model"].choices} == set(rapid.MODEL_LIMITS)
        assert {c.value for c in fields["edit_steps"].choices} == {20, 30, 40}
        assert fields["edit_steps"].default == 40
        assert len(fields) <= 25


def test_bot_rejects_fourth_edit_reference_before_downloading(bot_module, monkeypatch):
    interaction = SimpleNamespace(guild_id=123, response=SimpleNamespace(send_message=AsyncMock()))
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda _: object())
    asyncio.run(bot_module.generate(interaction, "edit", "test", [object()]*4, model=MODELS[0]))
    assert "at most 3" in interaction.response.send_message.call_args.args[0]


def test_lora_family_defaults_and_explicit_assignment_preserve_old_weights():
    config = copy.deepcopy(lora.DEFAULT_CONFIG)
    config["overrides"] = {"manual.safetensors": {"enabled": True, "strength": 0.45},
                           "qwen21/switched.safetensors": {"enabled": True, "strength": 0.7, "target": "qwen_edit"}}
    config = lora.parse_config(json.dumps(config))
    schema = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [["manual.safetensors", "qwen21/style.safetensors", "qwen21/switched.safetensors"]]}}}}
    assert [i["name"] for i in lora.selected_loras(schema, config, "qwen21")] == ["qwen21/style.safetensors"]
    assert [(i["name"], i["strength"]) for i in lora.selected_loras(schema, config, MODELS[0])] == [("manual.safetensors", 0.45), ("qwen21/switched.safetensors", 0.7)]
    config["overrides"]["manual.safetensors"]["target"] = "invalid"
    with pytest.raises(ValueError, match="Choose Qwen"):
        lora.parse_config(json.dumps(config))


@pytest.mark.parametrize("expansion", ["off", "standard", "heretic"])
def test_qwen21_loads_only_its_loras_before_the_cache(studio, monkeypatch, expansion):
    studio.initialize()
    graphs, _ = install_comfy_mock(studio, monkeypatch, ["root-edit.safetensors", "qwen21/a.safetensors", "qwen21/b.safetensors"])
    monkeypatch.setattr(studio, "rewrite_prompt", AsyncMock(return_value="expanded"))
    asyncio.run(studio.render_image("text", "test", model="qwen21", prompt_expansion=expansion))
    graph = graphs[0]
    assert graph["2000"]["inputs"]["model"] == ["1", 0]
    assert graph["2000"]["inputs"]["lora_name"] == "qwen21/a.safetensors"
    assert graph["2001"]["inputs"]["model"] == ["2000", 0]
    assert graph["4"]["inputs"]["model"] == ["2001", 0]
    assert graph["6"]["inputs"]["model"] == ["4", 0]
    assert "2002" not in graph


def test_admin_saves_lora_model_family_with_strength(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch, ["manual.safetensors"])
    with TestClient(studio.app) as client:
        token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text)[1]
        client.post("/login", data={"username": "admin", "password": "private-test-password-12345", "csrf_token": token})
        page = client.get("/admin/loras")
        values = {name: re.search(f'name="{name}" value="([^"]+)"', page.text)[1] for name in ("csrf_token", "revision")}
        response = client.post("/admin/loras", data={**values, "auto_enabled": "yes", "default_strength": "0.6",
                "filename": "manual.safetensors", "strength": "0.35", "active": "manual.safetensors", "target": "qwen21"})
        assert "settings saved" in response.text
        config = lora.read_config(studio.db)
        assert config["overrides"]["manual.safetensors"] == {"enabled": True, "strength": 0.35, "target": "qwen21"}
        catalog = client.get("/internal/discord/loras", headers={"Authorization": "Bearer test-secret"}).json()
        assert catalog["items"][0]["target"] == "qwen21"


@pytest.mark.parametrize("precision,count,size", [("fp8", 3, 30172239743), ("bf16", 3, 50499508486), ("both", 4, 71033271303)])
def test_setup_profiles_include_shared_dependencies_once(precision, count, size):
    manifest = setup.load_manifest()
    selected = setup.qwen_edit_manifest(manifest, precision)
    assert len(selected["files"]) == count
    assert sum(i["size"] for i in selected["files"]) == size
    combined = setup.selected_manifest(manifest, True, precision)
    assert len(combined["files"]) == 14+count
    assert len({i["destination"] for i in combined["files"]}) == 14+count


def test_download_script_only_downloads_the_requested_models(monkeypatch):
    downloader = load_module("edit2511_downloader_under_test", ROOT / "scripts/download-qwen-edit-2511.py")
    monkeypatch.setattr(sys, "argv", ["download", "--project", str(ROOT), "--precision", "fp8"])
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "curl")
    calls = []
    monkeypatch.setattr(setup, "download_files", lambda root, manifest: calls.append((root, manifest)))
    downloader.main()
    assert len(calls) == 1
    assert {i["model"] for i in calls[0][1]["files"]} == {"qwen_edit_2511_fp8", "qwen_edit_2511_shared"}


@pytest.mark.parametrize("component,key,folder", [("1", "unet_name", "diffusion_models"), ("20", "clip_name", "text_encoders"), ("21", "vae_name", "vae")])
def test_missing_edit_dependency_reports_its_exact_location(studio, component, key, folder):
    graph = rapid.build_edit2511_workflow("text", [], "test")
    schema = {n["class_type"]: {"input": {"required": {}}} for n in graph.values()}
    schema[graph[component]["class_type"]]["input"]["required"][key] = [[]]
    filename = graph[component]["inputs"][key]
    with pytest.raises(RuntimeError, match="models/"+folder+"/"+re.escape(filename)):
        studio.validate_workflow(graph, schema)


def test_browser_controls_switch_steps_limits_and_keep_expansion_preferences():
    script = rapid.rapid_controls().split("<script>", 1)[1].split("</script>", 1)[0]
    harness = '''
const assert = require('node:assert/strict');
const model = {value:'qwen21',addEventListener:(event,fn)=>model.change=fn};
const label = {firstChild:{nodeType:3,textContent:''}};
const picker = {files:Array(4).fill({}),setCustomValidity:msg=>picker.error=msg,closest:()=>label,addEventListener:(event,fn)=>picker.change=fn};
const enhancer = {tagName:'SELECT',value:'standard'};
const rapidSteps={}, editSteps={}, rapidLabel={}, editLabel={}, help={}, editHelp={};
const form = {elements:{model,references:picker,prompt_expansion:enhancer,rapid_steps:rapidSteps,edit_steps:editSteps}};
const elements = {'generation-form':form,'rapid-steps-label':rapidLabel,'edit-steps-label':editLabel,'rapid-model-help':help,'edit-model-help':editHelp};
global.document={getElementById:id=>elements[id],addEventListener:(event,fn)=>fn()};
''' + script + '''
model.value='qwen_edit_2511_fp8';model.change();
assert.equal(editSteps.disabled,false);assert.equal(editLabel.hidden,false);assert.equal(rapidSteps.disabled,true);
assert.match(picker.error,/at most 3/);assert.equal(enhancer.value,'off');assert.equal(editHelp.hidden,false);
enhancer.value='heretic';model.value='qwen_edit_2511_bf16';model.change();assert.equal(enhancer.value,'off');
model.value='qwen_edit_2511_fp8';model.change();assert.equal(enhancer.value,'heretic');
picker.files=Array(3).fill({});picker.change();assert.equal(picker.error,'');
model.value='rapid_aio_v19';model.change();assert.equal(rapidSteps.disabled,false);assert.equal(editSteps.disabled,true);
model.value='qwen21';model.change();assert.equal(enhancer.value,'standard');assert.match(label.firstChild.textContent,/1–10/);
'''
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_server_comparison_script_submits_both_precisions_and_all_expansions(tmp_path, monkeypatch):
    helper = load_module("edit2511_gpu_comparison_under_test", ROOT / "scripts/test-qwen-edit-2511.py")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_SECRET", "test-secret")
    monkeypatch.setattr(sys, "argv", ["test", "--precision", "both", "--all-expansions", "--steps", "20"])
    payloads = []
    def request(url, secret, payload=None):
        assert secret == "test-secret"
        if url.endswith("generation-status"):
            return {"active_jobs": 0}
        if payload:
            payloads.append(payload)
            return {"job": {"id": payload["generation_job_id"], "state": "done", "expanded_prompt": "expanded"}}
        return png()
    monkeypatch.setattr(helper, "api_request", request)
    helper.main()
    assert {(p["model"], p["prompt_expansion"]) for p in payloads} == {(m, e) for m in MODELS for e in ("off", "standard", "heretic")}
    assert all(p["edit_steps"] == 20 and "rapid_steps" not in p for p in payloads)
    assert len(list(tmp_path.rglob("*.jpg"))) == len(list(tmp_path.rglob("*.json"))) == 6
