"""Qwen 2.1 encoder/sampling options through real web and Discord job paths."""
import asyncio
import copy
import json
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from test_rapid_v23 import ROOT, studio, bot_module, install_comfy_mock, png, setup, load_module
import qwen_quality as quality

OPTIONS = dict(qwen_sampler="er_sde", qwen_scheduler="beta", qwen_text_encoder="int8_convrot")


@pytest.mark.parametrize("encoder", ["bf16", "int8_convrot"])
@pytest.mark.parametrize("mode,count", [("text", 0), ("edit", 1), ("edit", 10)])
@pytest.mark.parametrize("expansion", ["off", "standard", "heretic"])
def test_render_keeps_references_expansion_loras_and_selected_encoder(studio, monkeypatch, encoder, mode, count, expansion):
    studio.initialize()
    graphs, uploads = install_comfy_mock(studio, monkeypatch, ["qwen21/style.safetensors", "edit.safetensors"])
    monkeypatch.setattr(studio, "rewrite_prompt", AsyncMock(return_value="rewritten"))
    references = [studio.validate_reference(png()) for _ in range(count)]
    asyncio.run(studio.render_image(mode, "original", references, seed=42, prompt_expansion=expansion,
                                  qwen_sampler="er_sde", qwen_scheduler="beta", qwen_text_encoder=encoder))
    graph = graphs[0]
    assert graph["2"]["inputs"]["clip_name"] == quality.QWEN21_TEXT_ENCODERS[encoder]
    assert graph["5"]["inputs"]["clip"] == ["2", 0]
    assert (graph["6"]["inputs"]["sampler_name"], graph["6"]["inputs"]["scheduler"]) == ("er_sde", "beta")
    assert graph["6"]["inputs"]["seed"] == 42
    assert graph["6"]["inputs"]["steps"] == 40
    assert len(uploads) == count
    assert sum(k.startswith("images.image_") for k in graph["5"]["inputs"]) == count
    assert graph["2000"]["inputs"]["lora_name"] == "qwen21/style.safetensors"
    assert graph["4"]["inputs"]["model"] == ["2000", 0]
    if expansion == "standard":
        assert graph["5"]["inputs"]["prompt"] == ["900", 0]
        assert graph["10"]["inputs"]["clip"] == ["9", 0]
        assert graph["9"]["inputs"]["clip_name"].startswith("qwen3.5_9b")
    else:
        assert "9" not in graph and "10" not in graph
        assert graph["5"]["inputs"]["prompt"] == ("rewritten" if expansion == "heretic" else "original")


def test_defaults_preserve_installed_sampling_and_input_template(studio):
    graph = studio.build_workflow(json.loads(studio.WORKFLOW_TEXT.read_text()), [], "test")
    graph["6"]["inputs"].update(sampler_name="dpmpp_2m", scheduler="karras")
    before = copy.deepcopy(graph)
    result = quality.configure_workflow(graph, "test", "standard", True, 42)
    assert (result["6"]["inputs"]["sampler_name"], result["6"]["inputs"]["scheduler"]) == ("dpmpp_2m", "karras")
    assert result["2"]["inputs"]["clip_name"] == quality.QWEN21_TEXT_ENCODERS["bf16"]
    assert graph == before


@pytest.mark.parametrize("bad", [dict(qwen_sampler="unknown"), dict(qwen_scheduler="unknown"),
                                 dict(qwen_text_encoder="../weights.safetensors"), dict(qwen_sampler=[]),
                                 dict(qwen_scheduler=True), dict(qwen_text_encoder=None)])
def test_invalid_options_are_rejected(bad):
    with pytest.raises(ValueError): quality.validate_qwen21_selection(**bad)


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
def test_discord_api_forwards_all_three_options(studio, monkeypatch, route):
    import generation_progress
    monkeypatch.setattr(studio, "JOBS", generation_progress.JobStore())
    renderer = AsyncMock(return_value=png())
    monkeypatch.setattr(studio, "render_image", renderer)
    with TestClient(studio.app) as client:
        response = client.post(route, json={"discord_user_id": "test", "mode": "text", "prompt": "test", **OPTIONS},
                               headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == (202 if route.endswith("jobs") else 200)
        if route.endswith("jobs"):
            for _ in range(100):
                status = client.get(f"{route}/{response.json()['job']['id']}?discord_user_id=test",
                                    headers={"Authorization": "Bearer test-secret"}).json()["job"]
                if status["state"] == "done": break
            assert status["state"] == "done"
    for k, v in OPTIONS.items(): assert renderer.call_args.kwargs[k] == v


@pytest.mark.parametrize("route", ["/generate", "/generation-jobs"])
def test_website_form_forwards_all_three_options(studio, monkeypatch, route):
    import generation_progress
    store = generation_progress.JobStore()
    monkeypatch.setattr(studio, "JOBS", store)
    monkeypatch.setattr(generation_progress, "JOBS", store)
    renderer = AsyncMock(return_value=png())
    monkeypatch.setattr(studio, "render_image", renderer)
    with TestClient(studio.app) as client:
        token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text)[1]
        home = client.post("/login", data={"username": "admin", "password": "private-test-password-12345", "csrf_token": token})
        for key in OPTIONS: assert f'name="{key}"' in home.text
        token = re.search(r'name="csrf_token" value="([^"]+)"', home.text)[1]
        response = client.post(route, data={"csrf_token": token, "mode": "text", "prompt": "test",
                                           "generation_job_id": "e"*32, **OPTIONS})
        assert response.status_code == (202 if route.endswith("jobs") else 200)
        if route.endswith("jobs"):
            for _ in range(100):
                state = client.get(f"{route}/{response.json()['job']['id']}").json()["job"]["state"]
                if state == "done": break
            assert state == "done"
    for k, v in OPTIONS.items(): assert renderer.call_args.kwargs[k] == v


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
@pytest.mark.parametrize("extra", [{"qwen_sampler": "bad"}, {"qwen_scheduler": "bad"}, {"qwen_text_encoder": "bad"},
                                  {"model": "rapid_aio_v19", **OPTIONS}, {"model": "qwen_edit_2511_bf16", **OPTIONS}])
def test_invalid_or_inapplicable_api_options_never_render(studio, monkeypatch, route, extra):
    renderer = AsyncMock()
    monkeypatch.setattr(studio, "render_image", renderer)
    with TestClient(studio.app) as client:
        response = client.post(route, json={"discord_user_id": "test", "mode": "text", "prompt": "test", **extra},
                               headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 400
    renderer.assert_not_awaited()


@pytest.mark.parametrize("missing", ["encoder", "sampler", "scheduler"])
def test_missing_comfy_option_fails_before_queueing(studio, monkeypatch, missing):
    studio.initialize()
    graphs, _ = install_comfy_mock(studio, monkeypatch)
    original = studio.validate_workflow
    def validate(graph, schema):
        schema = copy.deepcopy(schema)
        if missing == "encoder":
            schema["CLIPLoader"]["input"]["required"]["clip_name"] = [[quality.QWEN21_TEXT_ENCODERS["bf16"], graph.get("9", {}).get("inputs", {}).get("clip_name")]]
        else:
            field = "sampler_name" if missing == "sampler" else "scheduler"
            schema["KSampler"]["input"]["required"][field] = [["euler" if missing == "sampler" else "simple"]]
        original(graph, schema)
    monkeypatch.setattr(studio, "validate_workflow", validate)
    with pytest.raises(RuntimeError, match="Download it|invalid"):
        asyncio.run(studio.render_image("text", "test", prompt_expansion="off", **OPTIONS))
    assert not graphs


@pytest.mark.parametrize("command_name", ["imagine", "edit"])
def test_slash_commands_register_choices_and_forward_them(bot_module, monkeypatch, command_name):
    command = bot_module.bot.tree.get_command(command_name)
    fields = {p.name: p for p in command.parameters}
    assert len(fields) <= 25
    assert {c.value for c in fields["qwen_sampler"].choices} == {"default", "euler", "er_sde"}
    assert {c.value for c in fields["qwen_scheduler"].choices} == {"default", "simple", "beta"}
    assert {c.value for c in fields["qwen_text_encoder"].choices} == {"bf16", "int8_convrot"}
    generate = AsyncMock()
    monkeypatch.setattr(bot_module, "generate", generate)
    kwargs = {"reference": object()} if command_name == "edit" else {}
    asyncio.run(command.callback(object(), "test", **kwargs, **OPTIONS))
    for k, v in OPTIONS.items(): assert generate.call_args.kwargs[k] == v


def test_optional_encoder_is_pinned_and_downloaded_only_when_requested(monkeypatch):
    manifest = setup.load_manifest()
    selected = setup.qwen21_encoder_manifest(manifest)
    assert len(selected["files"]) == 1
    item = selected["files"][0]
    assert item["size"] == 10985825432
    assert item["sha256"] == "5deb0b5742b7ac2b1d2a9c6c48433a98b9444954b6114b616812ebd5d977296e"
    assert item["revision"] == "161a0674c90d4aa82cd19d9b02630263314451e5"
    assert item not in setup.selected_manifest(manifest)["files"]
    assert item in setup.selected_manifest(manifest, qwen21_int8_encoder=True)["files"]
    downloader = load_module("qwen21_encoder_downloader", ROOT / "scripts/download-qwen21-encoder.py")
    monkeypatch.setattr(sys, "argv", ["download", "--project", str(ROOT)])
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "curl")
    downloaded = []
    monkeypatch.setattr(setup, "download_files", lambda root, selected: downloaded.extend(selected["files"]))
    downloader.main()
    assert downloaded == [item]


def test_setup_encoder_flag_and_download_plan_are_offline():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/setup.py"), "--project", str(ROOT), "--check", "--qwen21-int8-encoder"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "qwen3vl_8b_int8_convrot.safetensors" in result.stdout
    result = subprocess.run([sys.executable, str(ROOT / "scripts/download-qwen21-encoder.py"), "--project", str(ROOT), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "10.99 GB" in result.stdout


def test_server_comparison_creates_four_distinct_images_with_identical_seed(tmp_path, monkeypatch):
    compare = load_module("qwen21_options_server_compare", ROOT / "scripts/test-qwen21-options.py")
    monkeypatch.setenv("DISCORD_BOT_SECRET", "private-test-secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["test", "--compare-defaults"])
    submitted = []
    def api(url, secret, payload=None):
        assert secret == "private-test-secret"
        if url.endswith("generation-status"): return {"active_jobs": 0}
        if payload:
            submitted.append(payload)
            return {"job": {"id": payload["generation_job_id"], "state": "done"}}
        if "/image?" in url: return png()
        raise AssertionError(url)
    monkeypatch.setattr(compare, "api_request", api)
    compare.main()
    assert len(submitted) == 4
    assert {p["seed"] for p in submitted} == {12345}
    assert {p["prompt"] for p in submitted} == {submitted[0]["prompt"]}
    assert {p["model"] for p in submitted} == {"qwen21"}
    assert {(p["qwen_text_encoder"], p["qwen_sampler"], p["qwen_scheduler"]) for p in submitted} == {
        (encoder, sampler, scheduler) for encoder in ("bf16", "int8_convrot")
        for sampler, scheduler in (("default", "default"), ("er_sde", "beta"))}
    assert len(list(tmp_path.rglob("*.jpg"))) == len(list(tmp_path.rglob("*.json"))) == 4
