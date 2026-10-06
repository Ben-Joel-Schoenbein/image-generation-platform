"""Auto discovery, persistent strengths, permissions and web/Discord AIO integration."""
import asyncio
import base64
import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from test_rapid_v23 import studio, bot_module, install_comfy_mock, png, rapid, heretic, MODELS
import lora_support as lora


def schema(names):
    return {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [names]}}}}


def login(client):
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text)[1]
    response = client.post("/login", data={"username": "admin", "password": "private-test-password-12345", "csrf_token": token})
    assert response.status_code == 200


def form_values(client):
    page = client.get("/admin/loras")
    assert page.status_code == 200
    return {name: re.search(r'name="' + name + r'" value="([^"]+)"', page.text)[1] for name in ("csrf_token", "revision")}


def test_new_files_sorted_and_automatically_active():
    config = lora.parse_config()
    names = lora.catalog_from_schema(schema(["z.safetensors", "folder/first.safetensors", "z.safetensors"]))
    assert names == ["folder/first.safetensors", "z.safetensors"]
    assert [(item["name"], item["strength"]) for item in lora.selected_loras(schema(names), config)] == [(name, 0.6) for name in names]


@pytest.mark.parametrize("value", [True, None, "nan", "inf", "-inf", "wrong", 2.1, -2.1])
def test_bad_strengths_refused(value):
    with pytest.raises(ValueError, match="strength"):
        lora.strength(value)


@pytest.mark.parametrize("name", ["", "..", ".", "../bad.safetensors", "/outside.safetensors", "sub/../../bad", "bad\\file.safetensors", "bad\x00file"])
def test_unsafe_file_names_refused(name):
    with pytest.raises(ValueError):
        lora.filename(name)


def test_overrides_zero_disabled_global_switch_and_missing_files():
    config = lora.parse_config()
    config["overrides"] = {"a.safetensors": {"enabled": True, "strength": -0.3},
                           "b.safetensors": {"enabled": False, "strength": 0.7},
                           "c.safetensors": {"enabled": True, "strength": 0},
                           "missing.safetensors": {"enabled": True, "strength": 0.4}}
    result = lora.selected_loras(schema(["a.safetensors", "b.safetensors", "c.safetensors", "new.safetensors"]), config)
    assert [(item["name"], item["strength"]) for item in result] == [("a.safetensors", -0.3), ("new.safetensors", 0.6)]
    config["enabled"] = False
    assert lora.selected_loras({}, config) == []
    assert config["overrides"]["missing.safetensors"]["strength"] == 0.4


def test_unavailable_loader_is_reported_and_empty_catalog_is_valid():
    with pytest.raises(RuntimeError, match="loader is unavailable"):
        lora.catalog_from_schema({})
    assert lora.catalog_from_schema(schema([])) == []


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode", ["text", "edit"])
@pytest.mark.parametrize("expansion", ["off", "standard", "heretic"])
def test_actual_render_stacks_all_detected_loras_with_prompt_expansion(studio, monkeypatch, model, mode, expansion):
    studio.initialize()
    names = ["z.safetensors", "sub/first.safetensors"]
    graphs, _ = install_comfy_mock(studio, monkeypatch, names)
    rewrite = AsyncMock(return_value="rewritten")
    monkeypatch.setattr(studio, "rewrite_prompt", rewrite)
    references = [studio.validate_reference(png())] if mode == "edit" else []
    asyncio.run(studio.render_image(mode, "test", references, model=model, seed=12, prompt_expansion=expansion))
    graph = graphs[0]
    assert graph["2000"]["inputs"] == {"model": ["1", 0], "lora_name": "sub/first.safetensors", "strength_model": 0.6}
    assert graph["2001"]["inputs"]["model"] == ["2000", 0]
    assert graph["2001"]["inputs"]["lora_name"] == "z.safetensors"
    assert graph["5"]["inputs"]["model"] == ["2001", 0]
    assert graph["3"]["inputs"]["clip"] == ["1", 1]
    if expansion == "standard":
        assert graph["3"]["inputs"]["prompt"] == ["900", 0]
        assert graph["900"]["inputs"]["text"] == ["12", 0]
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list): assert value[0] in graph


def test_new_file_discovered_on_next_job_without_restart(studio, monkeypatch):
    studio.initialize()
    names = []
    graphs, _ = install_comfy_mock(studio, monkeypatch, names)
    asyncio.run(studio.render_image("text", "test", model=MODELS[0]))
    names.append("new.safetensors")
    asyncio.run(studio.render_image("text", "test", model=MODELS[0]))
    assert graphs[0]["5"]["inputs"]["model"] == ["1", 0]
    assert graphs[1]["2000"]["inputs"]["lora_name"] == "new.safetensors"


@pytest.mark.parametrize("mode", ["text", "edit"])
def test_website_generation_applies_loras_and_saves_image(studio, monkeypatch, mode):
    graphs, _ = install_comfy_mock(studio, monkeypatch, ["style.safetensors"])
    with TestClient(studio.app) as client:
        login(client)
        response = client.post("/generate", data={"mode": mode, "prompt": "test", "model": MODELS[1],
                               "csrf_token": form_values(client)["csrf_token"], "prompt_expansion": "off"},
                               files=[("references", ("reference.png", png(), "image/png"))] if mode == "edit" else None)
        assert response.status_code == 200 and "Image ready" in response.text
        assert graphs[0]["2000"]["inputs"]["lora_name"] == "style.safetensors"
        assert len(list(studio.DATA_DIR.glob("images/*/*.png"))) == 1


def test_standard_qwen_does_not_apply_automatic_aio_loras(studio, monkeypatch):
    studio.initialize()
    graphs, _ = install_comfy_mock(studio, monkeypatch, ["style.safetensors"])
    asyncio.run(studio.render_image("text", "test", model="qwen21", prompt_expansion="off"))
    assert not any(node["class_type"] == "LoraLoaderModelOnly" for node in graphs[0].values())


def test_invalid_config_version_is_rejected():
    config = {**lora.DEFAULT_CONFIG, "version": True}
    with pytest.raises(ValueError, match="settings"):
        lora.parse_config(json.dumps(config))


def test_migrated_defaults_seed_database_only_once(studio, monkeypatch, tmp_path):
    path = tmp_path / "migrated-defaults.json"
    config = lora.parse_config()
    config["overrides"] = {"manual.safetensors": {"enabled": True, "strength": 0.45}}
    path.write_text(json.dumps(config))
    monkeypatch.setattr(lora, "DEFAULTS_PATH", path)
    studio.initialize()
    assert lora.read_config(studio.db) == config
    config["overrides"]["manual.safetensors"]["strength"] = 0.8
    path.write_text(json.dumps(config))
    studio.initialize()
    assert lora.read_config(studio.db)["overrides"]["manual.safetensors"]["strength"] == 0.45


def test_settings_page_saves_per_file_strengths_and_retains_missing(studio, monkeypatch):
    names = ["a.safetensors", "sub/b.safetensors"]
    graphs, _ = install_comfy_mock(studio, monkeypatch, names)
    with TestClient(studio.app) as client:
        login(client)
        values = form_values(client)
        response = client.post("/admin/loras", data={**values, "auto_enabled": "yes", "default_strength": "0.4",
                               "filename": names, "strength": ["0.8", "0.2"], "active": [names[0]]})
        assert "LoRA settings saved" in response.text
        config = lora.read_config(studio.db)
        assert config["overrides"] == {names[0]: {"enabled": True, "strength": 0.8}, names[1]: {"enabled": False, "strength": 0.2}}
        names.remove("sub/b.safetensors")
        page = client.get("/admin/loras")
        assert "saved strengths are retained" in page.text
        assert "sub/b.safetensors" in page.text
        names.append("new.safetensors")
        response = client.post("/internal/discord/generate", json={"discord_user_id": "123", "mode": "text", "prompt": "test", "model": MODELS[0]}, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 200
        assert graphs[0]["2000"]["inputs"]["strength_model"] == 0.8
        assert graphs[0]["2001"]["inputs"]["strength_model"] == 0.4
        studio.initialize()
        assert lora.read_config(studio.db) == config


def test_admin_can_disable_every_lora_and_reenable_it(studio, monkeypatch):
    graphs, _ = install_comfy_mock(studio, monkeypatch, ["a.safetensors"])
    with TestClient(studio.app) as client:
        login(client)
        client.post("/admin/loras", data={**form_values(client), "default_strength": "0.6"})
        assert lora.read_config(studio.db)["enabled"] is False
        response = client.post("/internal/discord/generate", json={"discord_user_id": "123", "mode": "text", "prompt": "test", "model": MODELS[0]}, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 200
        assert graphs[0]["5"]["inputs"]["model"] == ["1", 0]
        client.post("/admin/loras", data={**form_values(client), "auto_enabled": "yes", "default_strength": "0.7"})
        assert lora.read_config(studio.db)["enabled"] is True


def test_invalid_strength_and_stale_revision_cannot_overwrite_settings(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        login(client)
        values = form_values(client)
        before = lora.read_config(studio.db)
        response = client.post("/admin/loras", data={**values, "auto_enabled": "yes", "default_strength": "nan"})
        assert "number between -2 and 2" in response.text
        assert lora.read_config(studio.db) == before
        client.post("/admin/loras", data={**values, "auto_enabled": "yes", "default_strength": "0.8"})
        response = client.post("/admin/loras", data={**values, "auto_enabled": "yes", "default_strength": "0.2"})
        assert "settings changed" in response.text
        assert lora.read_config(studio.db)["default_strength"] == 0.8


def test_admin_routes_require_login_privileges_and_csrf(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch)
    with TestClient(studio.app) as client:
        assert client.get("/admin/loras", follow_redirects=False).status_code == 303
        assert client.post("/admin/loras", data={}).status_code == 403
        login(client)
        before = lora.read_config(studio.db)
        response = client.post("/admin/loras", data={"default_strength": "0.9"})
        assert "Refresh" in response.text or "CSRF" in response.text or "Invalid" in response.text
        assert lora.read_config(studio.db) == before
        monkeypatch.setattr(lora, "fetch_catalog", AsyncMock(side_effect=AssertionError("No worker access for unauthorized users")))
        with studio.db() as conn:
            conn.execute("UPDATE users SET is_admin=0 WHERE username='admin'")
        assert client.get("/admin/loras").status_code == 403
        assert client.post("/admin/loras", data={}).status_code == 403


def test_discord_catalog_requires_secret_and_reports_active_strengths(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch, ["a.safetensors", "sub/b.safetensors"])
    with TestClient(studio.app) as client:
        assert client.get("/internal/discord/loras").status_code == 401
        result = client.get("/internal/discord/loras", headers={"Authorization": "Bearer test-secret"})
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert [(item["name"], item["strength"], item["active"]) for item in result.json()["items"]] == [("a.safetensors", 0.6, True), ("sub/b.safetensors", 0.6, True)]


def test_discovery_failure_can_be_disabled_without_losing_settings(studio, monkeypatch):
    install_comfy_mock(studio, monkeypatch)
    monkeypatch.setattr(lora, "fetch_catalog", AsyncMock(side_effect=RuntimeError("worker unavailable")))
    with TestClient(studio.app) as client:
        login(client)
        response = client.get("/admin/loras")
        assert "file list unavailable" in response.text
        client.post("/admin/loras", data={**form_values(client), "default_strength": "0.6"})
        assert lora.read_config(studio.db)["enabled"] is False
        assert client.get("/internal/discord/loras", headers={"Authorization": "Bearer test-secret"}).status_code == 502


def test_settings_markup_escapes_names_and_preserves_form_values():
    markup = lora.settings_markup(['folder/"<script>&.safetensors'], lora.parse_config(), 'token"<>')
    assert '<script>' not in markup
    assert '&lt;script&gt;' in markup
    assert 'name="strength" value="0.6"' in markup
    assert 'name="auto_enabled" value="yes" checked' in markup


def test_lora_chain_avoids_node_collisions_and_zero_skips_loading():
    graph = rapid.build_rapid_workflow("text", [], "test")
    graph["2000"] = {"class_type": "Existing", "inputs": {}}
    lora.apply_loras(graph, [{"name": "zero.safetensors", "strength": 0}, {"name": "active.safetensors", "strength": 0.5}])
    assert graph["2000"]["class_type"] == "Existing"
    assert graph["2001"]["inputs"]["model"] == ["1", 0]
    assert graph["5"]["inputs"]["model"] == ["2001", 0]


def test_duplicate_loras_rejected_before_changing_graph():
    graph = rapid.build_rapid_workflow("text", [], "test")
    before = copy.deepcopy(graph)
    with pytest.raises(ValueError, match="only once"):
        lora.apply_loras(graph, [{"name": "a.safetensors", "strength": 0.5}] * 2)
    assert graph == before


@pytest.mark.parametrize("route", ["/internal/discord/generate", "/internal/discord/jobs"])
def test_real_discord_job_api_applies_automatic_loras(studio, monkeypatch, route):
    import generation_progress
    monkeypatch.setattr(studio, "JOBS", generation_progress.JobStore())
    graphs, _ = install_comfy_mock(studio, monkeypatch, ["a.safetensors"])
    with TestClient(studio.app) as client:
        response = client.post(route, headers={"Authorization": "Bearer test-secret"},
                               json={"discord_user_id": "123", "mode": "text", "prompt": "test", "model": MODELS[1], "generation_job_id": "b" * 32})
        assert response.status_code == (202 if route.endswith("jobs") else 200)
        if route.endswith("jobs"):
            for _ in range(100):
                job = client.get(route + "/" + "b" * 32 + "?discord_user_id=123", headers={"Authorization": "Bearer test-secret"}).json()["job"]
                if job["state"] in {"done", "error"}: break
            assert job["state"] == "done", job
    assert graphs[0]["2000"]["inputs"]["lora_name"] == "a.safetensors"


def test_discord_command_lists_catalog_without_mentions(bot_module, monkeypatch):
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {"enabled": True, "default_strength": 0.6, "items": [{"name": "@everyone.safetensors", "strength": 0.8, "active": True}]}
    class Session:
        def get(self, url, headers):
            assert url.endswith("/internal/discord/loras")
            assert headers["Authorization"] == "Bearer test-secret"
            return Response()
    monkeypatch.setattr(bot_module.bot, "api_session", Session())
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda _: object())
    interaction = SimpleNamespace(guild_id=123, response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    command = bot_module.bot.tree.get_command("loras")
    assert command is not None
    asyncio.run(command.callback(interaction))
    sent = interaction.followup.send.call_args
    assert "0.8 (active; Edit 2511 / Rapid AIO)" in sent.args[0]
    assert sent.kwargs["ephemeral"] is True
    assert sent.kwargs["allowed_mentions"].everyone is False
