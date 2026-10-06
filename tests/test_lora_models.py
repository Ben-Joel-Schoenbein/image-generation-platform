"""Per-model LoRA selection, compatibility with old settings and real job routes."""
import asyncio
import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from test_rapid_v23 import studio, bot_module, install_comfy_mock, png, rapid
from test_loras import login, form_values
import lora_support as lora

MODELS = list(lora.MODEL_LABELS)


def save_config(studio, overrides):
    config = copy.deepcopy(lora.DEFAULT_CONFIG)
    config["overrides"] = overrides
    with studio.db() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(config), lora.CONFIG_KEY))
    return config


def test_models_match_the_web_and_discord_catalogs(bot_module):
    assert set(MODELS) == set(rapid.MODEL_LIMITS)
    assert set(MODELS) == {choice.value for choice in bot_module.MODEL_CHOICES}


def test_old_config_keeps_strengths_and_effective_assignments():
    config = copy.deepcopy(lora.DEFAULT_CONFIG)
    config["overrides"] = {"root.safetensors": {"enabled": True, "strength": 0.45},
                           "qwen21/overridden.safetensors": {"enabled": False, "strength": -0.3, "target": "qwen_edit"},
                           "elsewhere/qwen.safetensors": {"enabled": True, "strength": 0.8, "target": "qwen21"}}
    parsed = lora.parse_config(json.dumps(config))
    assert parsed == config
    items = {i["name"]: i for i in lora.catalog_items([*config["overrides"], "qwen21/new.safetensors"], parsed)}
    assert items["root.safetensors"]["models"] == lora.FAMILY_MODELS["qwen_edit"]
    assert items["qwen21/overridden.safetensors"]["models"] == lora.FAMILY_MODELS["qwen_edit"]
    assert items["elsewhere/qwen.safetensors"]["models"] == ["qwen21"]
    assert items["qwen21/new.safetensors"]["models"] == ["qwen21"]
    assert parsed == config


@pytest.mark.parametrize("bad", ["qwen21", None, [True], ["unknown"], ["qwen21", "qwen21"], {}, [123]])
def test_invalid_model_selection_is_rejected(bad):
    config = copy.deepcopy(lora.DEFAULT_CONFIG)
    config["overrides"] = {"a.safetensors": {"enabled": True, "strength": 0.6, "models": bad}}
    with pytest.raises(ValueError, match="model"):
        lora.parse_config(json.dumps(config))


def test_explicit_selection_overrides_the_folder_and_legacy_family():
    config = copy.deepcopy(lora.DEFAULT_CONFIG)
    config["overrides"] = {"qwen21/custom.safetensors": {"enabled": True, "strength": 0.7,
                              "target": "qwen21", "models": ["qwen_edit_2511_bf16", "rapid_aio_v23_nsfw"]},
                           "none.safetensors": {"enabled": True, "strength": 0.5, "models": []}}
    parsed = lora.parse_config(json.dumps(config))
    assert parsed["overrides"]["qwen21/custom.safetensors"]["models"] == ["rapid_aio_v23_nsfw", "qwen_edit_2511_bf16"]
    schema = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [[*config["overrides"]]]}}}}
    for model in MODELS:
        selected = lora.selected_loras(schema, parsed, model)
        assert [i["name"] for i in selected] == (["qwen21/custom.safetensors"] if model in {"rapid_aio_v23_nsfw", "qwen_edit_2511_bf16"} else [])
    assert lora.catalog_items(["none.safetensors"], parsed)[0]["active"] is False


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("expansion", ["off", "standard", "heretic"])
def test_actual_render_loads_only_the_selected_models_loras(studio, monkeypatch, model, expansion):
    studio.initialize()
    names = [f"lora{i}.safetensors" for i in range(len(MODELS))]
    graphs, _ = install_comfy_mock(studio, monkeypatch, names)
    config = save_config(studio, {name: {"enabled": True, "strength": (i+1)/10, "models": [m]}
                                 for i, (name, m) in enumerate(zip(names, MODELS))})
    monkeypatch.setattr(studio, "rewrite_prompt", AsyncMock(return_value="rewritten"))
    asyncio.run(studio.render_image("text", "test", model=model, seed=42, prompt_expansion=expansion))
    graph = graphs[0]
    patches = [n for n in graph.values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(patches) == 1
    index = MODELS.index(model)
    assert patches[0]["inputs"]["lora_name"] == names[index]
    assert patches[0]["inputs"]["strength_model"] == (index+1)/10
    studio.initialize()
    assert lora.read_config(studio.db) == config


def test_admin_checkboxes_save_multiple_models_and_preserve_missing_files(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch, ["a.safetensors", "b.safetensors"])
    with TestClient(studio.app) as client:
        login(client)
        save_config(studio, {"missing.safetensors": {"enabled": True, "strength": 0.9, "models": ["qwen21"]}})
        page = client.get("/admin/loras")
        assert 'name="model_selection" value="per_model_v1"' in page.text
        assert page.text.count('name="models_0"') == 5
        assert page.text.count('name="models_1"') == 5
        assert 'name="target"' not in page.text
        response = client.post("/admin/loras", data={**form_values(client), "auto_enabled": "yes", "default_strength": "0.6",
                "model_selection": "per_model_v1", "filename": ["a.safetensors", "b.safetensors"],
                "strength": ["0.35", "0.8"], "active": ["a.safetensors", "b.safetensors"],
                "models_0": ["rapid_aio_v23_nsfw", "qwen_edit_2511_bf16"]})
        assert "LoRA settings saved" in response.text
        config = lora.read_config(studio.db)
        assert config["overrides"]["a.safetensors"] == {"enabled": True, "strength": 0.35, "models": ["rapid_aio_v23_nsfw", "qwen_edit_2511_bf16"]}
        assert config["overrides"]["b.safetensors"]["models"] == []
        assert config["overrides"]["missing.safetensors"]["models"] == ["qwen21"]
        catalog = client.get("/internal/discord/loras", headers={"Authorization": "Bearer test-secret"}).json()
        assert catalog["items"][0]["models"] == ["rapid_aio_v23_nsfw", "qwen_edit_2511_bf16"]
        assert catalog["items"][1]["active"] is False


@pytest.mark.parametrize("extra", [{"models_0": "unknown"}, {"models_0": ["qwen21", "qwen21"]},
                                  {"models_1": "qwen21"}, {"model_selection": "wrong"}, {"target": "qwen21"}])
def test_invalid_model_form_cannot_change_configuration(studio, monkeypatch, extra):
    install_comfy_mock(studio, monkeypatch, ["a.safetensors"])
    with TestClient(studio.app) as client:
        login(client)
        before = lora.read_config(studio.db)
        response = client.post("/admin/loras", data={**form_values(client), "auto_enabled": "yes", "default_strength": "0.6",
                  "filename": "a.safetensors", "strength": "0.5", "active": "a.safetensors", "model_selection": "per_model_v1", **extra})
        assert "error" in response.text
        assert lora.read_config(studio.db) == before


def test_stale_model_selection_and_legacy_strength_save_preserve_current_selection(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch, ["a.safetensors"])
    with TestClient(studio.app) as client:
        login(client)
        old_form = form_values(client)
        payload = {**old_form, "auto_enabled": "yes", "default_strength": "0.6", "filename": "a.safetensors",
                   "strength": "0.4", "active": "a.safetensors", "model_selection": "per_model_v1", "models_0": "rapid_aio_v23_nsfw"}
        client.post("/admin/loras", data=payload)
        response = client.post("/admin/loras", data={**payload, "models_0": "qwen21"})
        assert "settings changed" in response.text
        assert lora.read_config(studio.db)["overrides"]["a.safetensors"]["models"] == ["rapid_aio_v23_nsfw"]
        # Old strength-only clients do not erase a newer precise assignment.
        response = client.post("/admin/loras", data={**form_values(client), "auto_enabled": "yes", "default_strength": "0.6",
                "filename": "a.safetensors", "strength": "0.7", "active": "a.safetensors"})
        assert "settings saved" in response.text
        item = lora.read_config(studio.db)["overrides"]["a.safetensors"]
        assert item == {"enabled": True, "strength": 0.7, "models": ["rapid_aio_v23_nsfw"]}


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
@pytest.mark.parametrize("model", ["rapid_aio_v19", "rapid_aio_v23_nsfw"])
def test_real_discord_job_routes_filter_per_model(studio, monkeypatch, route, model):
    import generation_progress
    monkeypatch.setattr(studio, "JOBS", generation_progress.JobStore())
    graphs, _ = install_comfy_mock(studio, monkeypatch, ["v23-only.safetensors"])
    with TestClient(studio.app) as client:
        save_config(studio, {"v23-only.safetensors": {"enabled": True, "strength": 0.8, "models": ["rapid_aio_v23_nsfw"]}})
        response = client.post(route, headers={"Authorization": "Bearer test-secret"}, json={
            "discord_user_id": "123", "generation_job_id": "e"*32, "mode": "text", "prompt": "test", "model": model, "prompt_expansion": "off"})
        assert response.status_code == (202 if route.endswith("jobs") else 200)
        if route.endswith("jobs"):
            for _ in range(100):
                state = client.get(f"{route}/{response.json()['job']['id']}?discord_user_id=123", headers={"Authorization": "Bearer test-secret"}).json()["job"]["state"]
                if state == "done": break
            assert state == "done"
    patches = [n for n in graphs[0].values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(patches) == (1 if model == "rapid_aio_v23_nsfw" else 0)


def test_discord_loras_lists_exact_model_selections(bot_module, monkeypatch):
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {"enabled": True, "default_strength": 0.6, "items": [
            {"name": "only.safetensors", "strength": 0.8, "active": True, "models": ["rapid_aio_v23_nsfw"]},
            {"name": "none.safetensors", "strength": 0.4, "active": False, "models": []}]}
    class Session:
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(bot_module.bot, "api_session", Session())
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda _: object())
    interaction = SimpleNamespace(guild_id=123, response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(bot_module.bot.tree.get_command("loras").callback(interaction))
    text = interaction.followup.send.call_args.args[0]
    assert "Qwen Rapid AIO v23" in text
    assert "Qwen Rapid AIO v19" not in text
    assert "no models selected" in text
