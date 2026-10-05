import asyncio
# IMAGE_STUDIO_PROGRESS_V1
from discord_jobs import deliver_generation
# QWEN_21_QUALITY_UPGRADE_V1
import base64
import io
import os

import aiohttp
import discord
from discord import app_commands


TOKEN = os.getenv("DISCORD_TOKEN", "")
API_URL = os.getenv("IMAGE_STUDIO_API", "http://web:8000/internal/discord/generate")
API_SECRET = os.getenv("DISCORD_BOT_SECRET", "")
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024
MAX_TOTAL_UPLOAD = int(os.getenv("MAX_REFERENCE_TOTAL_MB", "60")) * 1024 * 1024
MAX_REFERENCES = 10
if not TOKEN or not API_SECRET:
    raise RuntimeError("Set the bot token and API secret")


class StudioBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.api_session = None
        self.legacy_commands_checked = False

    async def setup_hook(self):
        self.api_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        # Global commands are available wherever this bot is installed.
        await self.tree.sync()

    async def remove_legacy_guild_commands(self):
        """Remove previous server-specific copies of our two slash commands."""
        if self.legacy_commands_checked:
            return
        self.legacy_commands_checked = True
        for guild in self.guilds:
            try:
                commands = await self.tree.fetch_commands(guild=guild)
                for command in commands:
                    if command.name in {"imagine", "edit"} and command.type == discord.AppCommandType.chat_input:
                        await command.delete()
            except discord.HTTPException as exc:
                print(f"Could not remove legacy commands for {guild.name}: {exc}")

    async def close(self):
        if self.api_session:
            await self.api_session.close()
        await super().close()


bot = StudioBot()


async def allowed(interaction: discord.Interaction) -> bool:
    if interaction.guild_id is None or bot.get_guild(interaction.guild_id) is None:
        await interaction.response.send_message("Use this bot on a Discord server where it has been invited.", ephemeral=True)
        return False
    return True


async def generate(interaction: discord.Interaction, mode: str, prompt: str, references: list[discord.Attachment], **options):
    if not await allowed(interaction):
        return
    if mode == "edit" and not references:
        await interaction.response.send_message("Attach a reference image for `/edit`.", ephemeral=True)
        return
    if len(references) > MAX_REFERENCES:
        await interaction.response.send_message(f"Use at most {MAX_REFERENCES} reference images.", ephemeral=True)
        return
    if any(image.size > MAX_UPLOAD for image in references):
        await interaction.response.send_message(f"Each reference image must be at most {MAX_UPLOAD // (1024 * 1024)} MB.", ephemeral=True)
        return
    if sum(image.size for image in references) > MAX_TOTAL_UPLOAD:
        await interaction.response.send_message(f"Reference images together must be at most {MAX_TOTAL_UPLOAD // (1024 * 1024)} MB.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    payload = {"discord_user_id": str(interaction.user.id), "mode": mode, "prompt": prompt}
    payload.update(options)
    try:
        payload["references"] = []
        total_size = 0
        for reference in references:
            if reference.content_type and reference.content_type not in {"image/png", "image/jpeg", "image/webp"}:
                await interaction.followup.send("Use PNG, JPEG, or WebP reference images.", ephemeral=True)
                return
            raw = await reference.read(use_cached=True)
            total_size += len(raw)
            if len(raw) > MAX_UPLOAD or total_size > MAX_TOTAL_UPLOAD:
                await interaction.followup.send("Reference images exceed the upload limit.", ephemeral=True)
                return
            payload["references"].append({"image_b64": base64.b64encode(raw).decode("ascii"), "filename": reference.filename})
        await deliver_generation(interaction, bot.api_session, API_URL, API_SECRET, payload)
    except (aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException) as exc:
        await interaction.followup.send(f"Image service error: {str(exc)[:250]}", ephemeral=True)


@bot.tree.command(name="imagine", description="Generate an image from a text description")
@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.describe(prompt="Describe the image you want")
@app_commands.choices(quality=[app_commands.Choice(name="Standard (1K)", value="standard"), app_commands.Choice(name="High (2K)", value="high")])
async def imagine(interaction: discord.Interaction, prompt: str, quality: str = "standard", enhance_prompt: bool = True, seed: int | None = None):
    await generate(interaction, "text", prompt, [], quality=quality, enhance_prompt=enhance_prompt, seed=seed)


@bot.tree.command(name="edit", description="Create or edit an image using up to 10 reference images")
@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.describe(prompt="Describe the changes; refer to Image 1, Image 2, etc.", reference="Reference image 1",
                       reference2="Reference image 2", reference3="Reference image 3", reference4="Reference image 4",
                       reference5="Reference image 5", reference6="Reference image 6", reference7="Reference image 7",
                       reference8="Reference image 8", reference9="Reference image 9", reference10="Reference image 10")
@app_commands.choices(quality=[app_commands.Choice(name="Standard (1K)", value="standard"), app_commands.Choice(name="High (2K)", value="high")])
async def edit(interaction: discord.Interaction, prompt: str, reference: discord.Attachment,
               reference2: discord.Attachment | None = None, reference3: discord.Attachment | None = None,
               reference4: discord.Attachment | None = None, reference5: discord.Attachment | None = None,
               reference6: discord.Attachment | None = None, reference7: discord.Attachment | None = None,
               reference8: discord.Attachment | None = None, reference9: discord.Attachment | None = None,
               reference10: discord.Attachment | None = None, recreate: bool = False,
               quality: str = "standard", enhance_prompt: bool = True, seed: int | None = None):
    await generate(interaction, "edit", prompt, [image for image in
        (reference, reference2, reference3, reference4, reference5, reference6, reference7, reference8, reference9, reference10)
        if image is not None], quality=quality, enhance_prompt=enhance_prompt, seed=seed, edit_intent="recreate" if recreate else "edit")


@bot.event
async def on_ready():
    await bot.remove_legacy_guild_commands()
    print(f"Image Studio bot ready as {bot.user}")


bot.run(TOKEN)
