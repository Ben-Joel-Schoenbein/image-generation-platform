"""Wait for background image jobs without depending on a long-lived interaction token."""
import asyncio
import contextlib
import hashlib
import io
import time

import aiohttp
import discord


class ImageServiceError(RuntimeError):
    pass


async def request_json(session, method, url, **kwargs):
    async with session.request(method, url, **kwargs) as response:
        try:
            data = await response.json()
        except (ValueError, aiohttp.ContentTypeError) as exc:
            raise ImageServiceError(f"Image service returned HTTP {response.status} instead of a job result.") from exc
        if response.status >= 400:
            raise ImageServiceError(str(data.get("error", "Image service request failed"))[:500])
        return data


async def wait_for_job(session, api_url, secret, payload, update):
    endpoint = api_url.rstrip("/").rsplit("/", 1)[0] + "/jobs"
    headers = {"Authorization": f"Bearer {secret}"}
    # The API deduplicates this ID, including when a submission response is lost.
    for attempt in range(3):
        try:
            data = await request_json(session, "POST", endpoint, json=payload, headers=headers)
            break
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(attempt + 1)
    job_id = data["job"]["id"]
    params = {"discord_user_id": payload["discord_user_id"]}
    while True:
        try:
            data = await request_json(session, "GET", endpoint + "/" + job_id,
                                      headers=headers, params=params)
            job = data["job"]
            if job["state"] == "error":
                raise ImageServiceError(job.get("error") or "Image generation failed")
            if job["state"] == "done":
                async with session.get(endpoint + "/" + job_id + "/image", headers=headers, params=params) as response:
                    if response.status != 200:
                        raise ImageServiceError("The finished image could not be retrieved.")
                    return await response.read()
            await update(job)
            await asyncio.sleep(2)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await update({"label": "Reconnecting to the image worker", "detail": "Your accepted job continues.",
                          "elapsed": 0, "percent": None})
            await asyncio.sleep(3)


async def deliver_generation(interaction, session, api_url, secret, payload):
    channel = interaction.channel
    if channel is None:
        await interaction.followup.send("The Discord channel is unavailable.", ephemeral=True)
        return
    # This normal bot message can still be edited after the interaction expires.
    waiting = await channel.send(f"{interaction.user.mention} · Generating your image…",
                                 allowed_mentions=discord.AllowedMentions.none())
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=f"Generation started: {waiting.jump_url}")
    payload["generation_job_id"] = hashlib.sha256(f"discord:{interaction.id}".encode()).hexdigest()[:32]
    last_update = 0
    last_content = ""

    async def update(job):
        nonlocal last_update, last_content
        seconds = job.get("elapsed", 0)
        percent = f" · {job['percent']:g}%" if job.get("percent") is not None else ""
        content = (f"{interaction.user.mention} · {job['label']}{percent}\n"
                   f"Elapsed: {seconds // 60}:{seconds % 60:02d}")
        if job.get("detail"):
            content += "\n" + job["detail"]
        now = time.monotonic()
        if content != last_content and now - last_update >= 5:
            with contextlib.suppress(discord.HTTPException):
                await waiting.edit(content=content[:1900], allowed_mentions=discord.AllowedMentions.none())
            last_update, last_content = now, content

    try:
        image = await wait_for_job(session, api_url, secret, payload, update)
        attachment = discord.File(io.BytesIO(image), filename="generated.jpg")
        try:
            await waiting.edit(content=f"{interaction.user.mention} · Image ready",
                               attachments=[attachment], allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            attachment = discord.File(io.BytesIO(image), filename="generated.jpg")
            await channel.send(f"{interaction.user.mention} · Image ready", file=attachment,
                               allowed_mentions=discord.AllowedMentions.none())
    except (ImageServiceError, aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException, KeyError) as exc:
        with contextlib.suppress(discord.HTTPException):
            await waiting.edit(content=f"Image generation failed: {str(exc)[:1000]}",
                               allowed_mentions=discord.AllowedMentions.none())
