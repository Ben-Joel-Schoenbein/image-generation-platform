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
# QWEN_RAPID_AIO_V19_INTEGRATION_V1
# IMAGE_STUDIO_HERETIC_PE_V1


TOKEN = os.getenv("DISCORD_TOKEN", "")
API_URL = os.getenv("IMAGE_STUDIO_API", "http://web:8000/internal/discord/generate")
API_SECRET = os.getenv("DISCORD_BOT_SECRET", "")
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024
MAX_TOTAL_UPLOAD = int(os.getenv("MAX_REFERENCE_TOTAL_MB", "60")) * 1024 * 1024
MAX_REFERENCES = 4
RAPID_MODELS = {"rapid_aio_v19", "rapid_aio_v23_nsfw"}
EDIT2511_MODELS = {"qwen_edit_2511_fp8", "qwen_edit_2511_bf16"}
MODEL_CHOICES = [
    app_commands.Choice(name="Qwen Image 2.1", value="qwen21"),
    app_commands.Choice(name="Qwen Image Edit 2511 — FP8 Mixed", value="qwen_edit_2511_fp8"),
    app_commands.Choice(name="Qwen Image Edit 2511 — BF16", value="qwen_edit_2511_bf16"),
    app_commands.Choice(name="Qwen Rapid AIO v19 — NSFW", value="rapid_aio_v19"),
    app_commands.Choice(name="Qwen Rapid AIO v23 — NSFW", value="rapid_aio_v23_nsfw"),
]
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
    model = options.get("model", "qwen21")
    if model not in {"qwen21", *RAPID_MODELS, *EDIT2511_MODELS}:
        await interaction.response.send_message("Choose a supported image model.", ephemeral=True)
        return
    if model != "qwen21" and (options.get("qwen_sampler", "default") != "default" or options.get("qwen_scheduler", "default") != "default" or options.get("qwen_text_encoder", "bf16") != "bf16"):
        await interaction.response.send_message("Sampler, scheduler and text encoder options apply only to Qwen Image 2.1.", ephemeral=True)
        return
    reference_limit = 3 if model in EDIT2511_MODELS else 4 if model in RAPID_MODELS else MAX_REFERENCES
    if len(references) > reference_limit:
        await interaction.response.send_message(f"Use at most {reference_limit} reference images for this model.", ephemeral=True)
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
@app_commands.describe(prompt_expansion="Prompt expansion: Auto follows model defaults or enhance_prompt")
@app_commands.choices(prompt_expansion=[app_commands.Choice(name="Auto", value="auto"), app_commands.Choice(name="Off", value="off"), app_commands.Choice(name="Standard", value="standard"), app_commands.Choice(name="Heretic", value="heretic")])
@app_commands.describe(model="Qwen 2.1: 10 references; Rapid AIO: 4; Edit 2511: 3", rapid_steps="Rapid AIO sampling steps", edit_steps="Edit 2511 sampling steps (20, 30 or 40)")
@app_commands.choices(edit_steps=[app_commands.Choice(name="20 — fast", value=20), app_commands.Choice(name="30", value=30), app_commands.Choice(name="40 — recommended", value=40)])
@app_commands.choices(model=MODEL_CHOICES, rapid_steps=[app_commands.Choice(name="4 — fast", value=4), app_commands.Choice(name="6", value=6), app_commands.Choice(name="8 — more detail", value=8)])
@app_commands.describe(qwen_sampler="Qwen 2.1 only: sampler", qwen_scheduler="Qwen 2.1 only: scheduler", qwen_text_encoder="Qwen 2.1 only: encoder; INT8 requires the extra download")
@app_commands.choices(qwen_sampler=[app_commands.Choice(name="Default — from workflow", value="default"), app_commands.Choice(name="Euler", value="euler"), app_commands.Choice(name="ER-SDE", value="er_sde")], qwen_scheduler=[app_commands.Choice(name="Default — from workflow", value="default"), app_commands.Choice(name="Simple", value="simple"), app_commands.Choice(name="Beta", value="beta")], qwen_text_encoder=[app_commands.Choice(name="Qwen3-VL 8B — BF16", value="bf16"), app_commands.Choice(name="Qwen3-VL 8B — INT8 ConvRot", value="int8_convrot")])
@app_commands.choices(quality=[app_commands.Choice(name="Standard (1K)", value="standard"), app_commands.Choice(name="High (2K)", value="high")])
async def imagine(interaction: discord.Interaction, prompt: str, quality: str = "standard", enhance_prompt: bool | None = None, seed: int | None = None, model: str = "qwen21", rapid_steps: int = 4, edit_steps: int = 40, prompt_expansion: str = "auto", qwen_sampler: str = "default", qwen_scheduler: str = "default", qwen_text_encoder: str = "bf16"):
    await generate(interaction, "text", prompt, [], quality=quality, enhance_prompt=(enhance_prompt if enhance_prompt is not None else model == "qwen21"), seed=seed, model=model, rapid_steps=rapid_steps, edit_steps=edit_steps, prompt_expansion=prompt_expansion, qwen_sampler=qwen_sampler, qwen_scheduler=qwen_scheduler, qwen_text_encoder=qwen_text_encoder)


@bot.tree.command(name="edit", description="Create or edit an image using up to 10 reference images")
@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.describe(prompt="Describe the changes; refer to Image 1, Image 2, etc.", reference="Reference image 1",
                       reference2="Reference image 2", reference3="Reference image 3", reference4="Reference image 4",
                       reference5="Reference image 5", reference6="Reference image 6", reference7="Reference image 7",
                       reference8="Reference image 8", reference9="Reference image 9", reference10="Reference image 10")
@app_commands.describe(prompt_expansion="Prompt expansion: Auto follows model defaults or enhance_prompt")
@app_commands.choices(prompt_expansion=[app_commands.Choice(name="Auto", value="auto"), app_commands.Choice(name="Off", value="off"), app_commands.Choice(name="Standard", value="standard"), app_commands.Choice(name="Heretic", value="heretic")])
@app_commands.describe(model="Qwen 2.1: 10 references; Rapid AIO: 4; Edit 2511: 3", rapid_steps="Rapid AIO sampling steps", edit_steps="Edit 2511 sampling steps (20, 30 or 40)")
@app_commands.choices(edit_steps=[app_commands.Choice(name="20 — fast", value=20), app_commands.Choice(name="30", value=30), app_commands.Choice(name="40 — recommended", value=40)])
@app_commands.choices(model=MODEL_CHOICES, rapid_steps=[app_commands.Choice(name="4 — fast", value=4), app_commands.Choice(name="6", value=6), app_commands.Choice(name="8 — more detail", value=8)])
@app_commands.describe(qwen_sampler="Qwen 2.1 only: sampler", qwen_scheduler="Qwen 2.1 only: scheduler", qwen_text_encoder="Qwen 2.1 only: encoder; INT8 requires the extra download")
@app_commands.choices(qwen_sampler=[app_commands.Choice(name="Default — from workflow", value="default"), app_commands.Choice(name="Euler", value="euler"), app_commands.Choice(name="ER-SDE", value="er_sde")], qwen_scheduler=[app_commands.Choice(name="Default — from workflow", value="default"), app_commands.Choice(name="Simple", value="simple"), app_commands.Choice(name="Beta", value="beta")], qwen_text_encoder=[app_commands.Choice(name="Qwen3-VL 8B — BF16", value="bf16"), app_commands.Choice(name="Qwen3-VL 8B — INT8 ConvRot", value="int8_convrot")])
@app_commands.choices(quality=[app_commands.Choice(name="Standard (1K)", value="standard"), app_commands.Choice(name="High (2K)", value="high")])
async def edit(interaction: discord.Interaction, prompt: str, reference: discord.Attachment,
               reference2: discord.Attachment | None = None, reference3: discord.Attachment | None = None,
               reference4: discord.Attachment | None = None, reference5: discord.Attachment | None = None,
               reference6: discord.Attachment | None = None, reference7: discord.Attachment | None = None,
               reference8: discord.Attachment | None = None, reference9: discord.Attachment | None = None,
               reference10: discord.Attachment | None = None, recreate: bool = False,
               quality: str = "standard", enhance_prompt: bool | None = None, seed: int | None = None, model: str = "qwen21", rapid_steps: int = 4, edit_steps: int = 40, prompt_expansion: str = "auto", qwen_sampler: str = "default", qwen_scheduler: str = "default", qwen_text_encoder: str = "bf16"):
    await generate(interaction, "edit", prompt, [image for image in
        (reference, reference2, reference3, reference4, reference5, reference6, reference7, reference8, reference9, reference10)
        if image is not None], quality=quality, enhance_prompt=(enhance_prompt if enhance_prompt is not None else model == "qwen21"), seed=seed, edit_intent="recreate" if recreate else "edit", model=model, rapid_steps=rapid_steps, edit_steps=edit_steps, prompt_expansion=prompt_expansion, qwen_sampler=qwen_sampler, qwen_scheduler=qwen_scheduler, qwen_text_encoder=qwen_text_encoder)


@bot.tree.command(name="loras", description="Show detected LoRAs, selected models and configured strengths")
@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
async def list_loras(interaction: discord.Interaction):
    if not await allowed(interaction):
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    endpoint = API_URL.rstrip("/").rsplit("/", 1)[0] + "/loras"
    try:
        async with bot.api_session.get(endpoint, headers={"Authorization": "Bearer " + API_SECRET}) as response:
            data = await response.json()
            if response.status != 200:
                raise RuntimeError(data.get("error", "LoRA discovery failed"))
        lines = ["**Qwen LoRAs**", "Automatic LoRAs: " + ("enabled" if data["enabled"] else "disabled"),
                 f"Default strength for new files: {data['default_strength']:g}"]
        items = data.get("items", [])
        omitted = 0
        for index, item in enumerate(items):
            name = discord.utils.escape_mentions(discord.utils.escape_markdown(item["name"]))
            if "models" in item:
                model_names = {choice.value: choice.name for choice in MODEL_CHOICES}
                assignment = ", ".join(model_names.get(model, model) for model in item["models"]) or "no models selected"
                assignment = discord.utils.escape_mentions(discord.utils.escape_markdown(assignment))
            else:
                assignment = 'Qwen 2.1' if item.get('target') == 'qwen21' else 'Edit 2511 / Rapid AIO'
            line = f"• {name}: {item['strength']:g} ({'active' if item['active'] else 'disabled'}; {assignment})"
            if len("\n".join(lines)) + len(line) > 1600:
                omitted = len(items) - index
                break
            lines.append(line)
        if omitted:
            lines.append(f"… {omitted} more files; view the complete list on the website.")
        if not items:
            lines.append("No LoRA files found in models/loras.")
        lines.append("The website administrator can choose models and strengths under Admin → LoRAs. /imagine and /edit apply only the LoRAs assigned to the selected model.")
        await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, RuntimeError) as exc:
        await interaction.followup.send("LoRA service error: " + str(exc)[:500], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.event
async def on_ready():
    await bot.remove_legacy_guild_commands()
    print(f"Image Studio bot ready as {bot.user}")


bot.run(TOKEN)
