import asyncio
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bot_module(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "test-token-no-network")
    monkeypatch.setenv("DISCORD_BOT_SECRET", "test-secret")
    monkeypatch.setattr(discord.Client, "run", lambda *args, **kwargs: None)
    spec = importlib.util.spec_from_file_location("bot_under_test", ROOT / "discord/bot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discord_registers_ten_attachment_options_for_guild_installs(bot_module):
    command = bot_module.bot.tree.get_command("edit")
    references = [parameter for parameter in command.parameters if parameter.type == discord.AppCommandOptionType.attachment]
    assert len(references) == 10
    assert references[0].required is True
    assert all(not parameter.required for parameter in references[1:])
    assert references[-1].name == "reference10"


def test_discord_downloads_and_submits_all_ten_attachments(bot_module, monkeypatch):
    payloads = []
    sent = []
    reads = []

    class ApiResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def read(self):
            return b"generated-image"

    class Api:
        def post(self, url, json, headers):
            payloads.append(json)
            assert headers["Authorization"] == "Bearer test-secret"
            return ApiResponse()

    class Attachment:
        content_type = "image/png"
        size = 4

        def __init__(self, i):
            self.filename = f"reference-{i}.png"
            self.i = i

        async def read(self, use_cached):
            reads.append(self.i)
            return str(self.i).encode()

    async def defer(**kwargs):
        pass

    async def send(*args, **kwargs):
        sent.append(kwargs)

    interaction = SimpleNamespace(guild_id=123, user=SimpleNamespace(id=456),
        response=SimpleNamespace(defer=defer, send_message=send), followup=SimpleNamespace(send=send))
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda guild_id: object())
    monkeypatch.setattr(bot_module.bot, "api_session", Api())
    command = bot_module.bot.tree.get_command("edit")
    asyncio.run(command.callback(interaction, "combine all images", *[Attachment(i) for i in range(10)]))
    assert reads == list(range(10))
    assert len(payloads[0]["references"]) == 10
    assert [base64.b64decode(image["image_b64"]) for image in payloads[0]["references"]] == [str(i).encode() for i in range(10)]
    assert "file" in sent[-1]


def test_bot_still_rejects_direct_messages(bot_module):
    sent = []

    async def send(message, **kwargs):
        sent.append(message)

    interaction = SimpleNamespace(guild_id=None, response=SimpleNamespace(send_message=send))
    assert asyncio.run(bot_module.allowed(interaction)) is False
    assert "Discord server" in sent[0]
