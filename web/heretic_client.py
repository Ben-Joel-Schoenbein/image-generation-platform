"""Optional GGUF prompt rewriting; called under Image Studio's generation lock."""
import asyncio
import base64
import io
import json
import math
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps
from rapid_aio import is_qwen_edit_model

MODELS = {"text": "pe-t2i", "edit": "pe-i2i"}
PROMPTS = Path(__file__).resolve().parent / "heretic_prompts"


def resolve_prompt_expansion(selection="auto", enhance_prompt=None, model="qwen21"):
    if selection not in {"auto", "off", "standard", "heretic"}:
        raise ValueError("Choose off, standard or heretic for prompt expansion")
    if selection != "auto":
        return selection
    enabled = enhance_prompt if enhance_prompt is not None else model == "qwen21"
    return "standard" if enabled else "off"


def parse_rewrite(content, finish_reason="stop"):
    if finish_reason == "length":
        raise RuntimeError("Heretic exhausted its token budget. Select Off or retry with a shorter description.")
    text = content.strip()
    if "<think>" in text:
        if "</think>" not in text:
            raise RuntimeError("Heretic returned reasoning without a final description.")
        text = text.rsplit("</think>", 1)[1].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        prompt = data["rewritten_prompt"]
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32000:
            raise ValueError("Invalid rewritten prompt")
        return prompt.strip()
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Heretic returned an invalid prompt result. Select Off or retry.") from exc


def reference_parts(references):
    parts = []
    for index, reference in enumerate(references, 1):
        with Image.open(io.BytesIO(reference.raw)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            width, height = image.size
            scale = min(1.0, math.sqrt(384 * 384 / (width * height)), 768 / max(width, height))
            image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90)
        parts.extend([
            {"type": "text", "text": f"Image {index}:"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")}},
        ])
    return parts


def capture_prompt(workflow, model):
    """Expose the actual Standard rewrite in ComfyUI history and websocket UI."""
    encoder = workflow["3" if is_qwen_edit_model(model) else "5"]
    if "900" in workflow:
        raise RuntimeError("Workflow node 900 is reserved for prompt preview")
    workflow["900"] = {"class_type": "StudioCapturePrompt", "inputs": {"text": encoder["inputs"]["prompt"]}}
    encoder["inputs"]["prompt"] = ["900", 0]


async def model_statuses(client, url):
    response = await client.get(url + "/models", timeout=20)
    response.raise_for_status()
    return {item["id"]: item.get("status", {}) for item in response.json()["data"]}


async def unload(client, url, name):
    response = await client.post(url + "/models/unload", json={"model": name}, timeout=30)
    response.raise_for_status()
    if response.json().get("success") is not True:
        raise RuntimeError("Heretic could not release its model; inspect the prompt-enhancer container.")
    deadline = time.monotonic() + 45
    while True:
        state = (await model_statuses(client, url)).get(name, {}).get("value")
        if state == "unloaded":
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Heretic model is still loaded; inspect the prompt-enhancer container.")
        await asyncio.sleep(.5)


async def release_comfy_models(client, comfy_url, progress):
    queue = await client.get(comfy_url + "/queue")
    queue.raise_for_status()
    if queue.json().get("queue_running") or queue.json().get("queue_pending"):
        raise RuntimeError("ComfyUI has another active job. Wait for it to finish before using Heretic.")
    response = await client.post(comfy_url + "/free", json={"unload_models": True, "free_memory": True})
    if response.status_code in {404, 405}:
        # Older installations expose /free; current OpenAPI names /api/free.
        response = await client.post(comfy_url + "/api/free", json={"unload_models": True, "free_memory": True})
    response.raise_for_status()
    deadline = time.monotonic() + 45
    minimum = float(os.getenv("HERETIC_MIN_FREE_GB", "16")) * 1024**3
    while True:
        stats = await client.get(comfy_url + "/system_stats")
        stats.raise_for_status()
        devices = stats.json().get("devices", [])
        cuda = [device for device in devices if device.get("type") == "cuda"]
        if not cuda:
            raise RuntimeError("Heretic GPU integration requires a working CUDA device in ComfyUI.")
        if cuda[0].get("vram_free", 0) >= minimum:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Not enough free GPU memory for Heretic. Inspect GPU usage or select Off.")
        if progress:
            progress.update("prompt", "Preparing Heretic", detail="Releasing the image model from GPU memory")
        await asyncio.sleep(1)


async def rewrite_prompt(mode, prompt, references, client, comfy_url, *, seed=None, progress=None):
    if mode not in MODELS:
        raise ValueError("Invalid generation mode")
    if mode == "edit" and not references:
        raise ValueError("Heretic I2I needs at least one reference image")
    kind = "t2i" if mode == "text" else "i2i"
    path = PROMPTS / kind / "system_prompt.txt"
    if not path.is_file():
        raise RuntimeError("Heretic system prompt is missing. Run deploy.sh to download and install the models.")
    system = path.read_text().strip()
    if len(system) < 1000:
        raise RuntimeError("Heretic system prompt is incomplete. Run deploy.sh again.")
    name = MODELS[mode]
    url = os.getenv("HERETIC_URL", "http://prompt-enhancer:8080").rstrip("/")
    started = time.monotonic()
    if progress:
        progress.prompt_expansion = "heretic"
        progress.update("prompt", "Preparing Heretic " + kind.upper())
    try:
        statuses = await model_statuses(client, url)
        if not set(MODELS.values()) <= statuses.keys():
            raise RuntimeError("Heretic model presets are missing. Run deploy.sh again.")
        for other, status in statuses.items():
            if other in MODELS.values() and status.get("value") != "unloaded":
                await unload(client, url, other)
        await release_comfy_models(client, comfy_url, progress)
        attempted = True
        try:
            loaded = await client.post(url + "/models/load", json={"model": name}, timeout=300)
            loaded.raise_for_status()
            if loaded.json().get("success") is not True:
                raise RuntimeError("Heretic could not load its model. Inspect the prompt-enhancer logs.")
            deadline = time.monotonic() + 300
            while True:
                status = (await model_statuses(client, url))[name]
                if status.get("value") == "loaded":
                    break
                if status.get("failed") or time.monotonic() >= deadline:
                    raise RuntimeError("Heretic model loading failed. Inspect the prompt-enhancer logs.")
                await asyncio.sleep(.5)
            user = reference_parts(references) + [{"type": "text", "text": prompt}]
            payload = {"model": name, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "max_tokens": 6144, "temperature": 1.0, "top_k": 20, "top_p": .95,
                       "min_p": 0.0, "presence_penalty": 1.5, "stream": True,
                       "stream_options": {"include_usage": True}}
            if seed is not None:
                payload["seed"] = seed
            chunks, reason, events = [], None, 0
            if progress:
                progress.update("prompt", "Expanding your description with Heretic " + kind.upper())
            # Bound the entire rewrite, including a stream that keeps producing tokens.
            async with asyncio.timeout(1200):
                async with client.stream("POST", url + "/v1/chat/completions", json=payload,
                                         timeout=httpx.Timeout(180, connect=20)) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        data = json.loads(raw)
                        if data.get("error"):
                            raise RuntimeError("Heretic reported an inference error. Inspect the prompt-enhancer logs.")
                        for choice in data.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content") or ""
                            if not isinstance(content, str):
                                raise RuntimeError("Heretic returned an invalid text stream")
                            chunks.append(content)
                            if content or delta.get("reasoning_content"):
                                events += 1
                                if progress:
                                    progress.detail = f"{events} text updates received · this optional step may take several minutes."
                                    progress.updated = time.monotonic()
                            if choice.get("finish_reason"):
                                reason = choice["finish_reason"]
            if reason != "stop":
                if reason == "length":
                    parse_rewrite("", reason)
                raise RuntimeError("Heretic stream ended before a complete result was confirmed. Retry or select Off.")
            rewritten = parse_rewrite("".join(chunks), reason)
        finally:
            if attempted:
                # The image graph is submitted only after model termination is confirmed.
                await unload(client, url, name)
        if progress:
            progress.expanded_prompt = rewritten
            progress.expansion_seconds = round(time.monotonic() - started, 1)
        return rewritten
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as exc:
        raise RuntimeError("Heretic prompt expansion failed. Inspect the prompt-enhancer logs or select Off.") from exc
