# IMAGE_STUDIO_HERETIC_PE_V1
"""Per-user background jobs and real ComfyUI progress for Image Studio."""
import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from websockets.asyncio.client import connect

LOG = logging.getLogger(__name__)
TERMINAL = {"done", "error"}


@dataclass
class GenerationJob:
    id: str
    user_id: int
    state: str = "running"
    stage: str = "waiting"
    label: str = "Waiting for the image worker"
    percent: float | None = None
    detail: str = ""
    result_url: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    finished: float | None = None
    prompt_id: str | None = None
    expanded_prompt: str | None = None
    prompt_expansion: str = "off"
    expansion_seconds: float | None = None
    image_bytes: bytes | None = field(default=None, repr=False)

    def update(self, stage, label, percent=None, detail=""):
        if self.state in TERMINAL:
            return
        self.stage, self.label = stage, label
        self.percent, self.detail = percent, detail
        self.updated = time.monotonic()

    def finish(self, result_url=None, error=None):
        self.state = "error" if error else "done"
        self.stage = self.state
        self.label = "Generation failed" if error else "Image ready"
        self.percent = None if error else 100
        self.detail = ""
        self.result_url, self.error = result_url, error
        self.finished = self.updated = time.monotonic()

    def snapshot(self):
        return {"id": self.id, "state": self.state, "stage": self.stage,
                "label": self.label, "percent": self.percent, "detail": self.detail,
                "result_url": self.result_url, "error": self.error,
                "expanded_prompt": self.expanded_prompt, "prompt_expansion": self.prompt_expansion,
                "expansion_seconds": self.expansion_seconds,
                "elapsed": round((self.finished or time.monotonic()) - self.created),
                "update_age": round(time.monotonic() - self.updated)}


class JobStore:
    def __init__(self):
        self.jobs = {}
        self.tasks = set()

    def active(self, user_id):
        return next((job for job in self.jobs.values()
                     if job.user_id == user_id and job.state not in TERMINAL), None)

    def get(self, job_id, user_id):
        job = self.jobs.get(job_id)
        return job if job and job.user_id == user_id else None

    def create(self, user_id, job_id):
        # No await between this check and insertion: atomic in the app's one event loop.
        existing = self.get(job_id, user_id) or self.active(user_id)
        if existing:
            return existing, False
        if not re.fullmatch(r"[a-f0-9]{32}", job_id or "") or job_id in self.jobs:
            raise ValueError("Invalid generation request ID")
        now = time.monotonic()
        self.jobs = {key: job for key, job in self.jobs.items()
                     if job.finished is None or now - job.finished < 3600}
        if len([job for job in self.jobs.values() if job.state not in TERMINAL]) >= 32:
            raise ValueError("The generation queue is full. Please try again later.")
        if len(self.jobs) >= 256:
            completed = [job for job in self.jobs.values() if job.finished is not None]
            if completed:
                del self.jobs[min(completed, key=lambda job: job.finished).id]
        job = GenerationJob(job_id, user_id)
        self.jobs[job.id] = job
        return job, True

    def launch(self, job, operation):
        async def run():
            try:
                result = await operation(job)
                if isinstance(result, bytes):
                    job.image_bytes = result
                    job.finish()
                else:
                    job.finish(result_url=result)
            except asyncio.CancelledError:
                job.finish(error="The web server restarted. Check your gallery before trying again.")
                raise
            except Exception as exc:
                LOG.exception("Image generation failed (job %s)", job.id)
                job.finish(error=str(exc)[:1400] or "Image generation failed")
        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


JOBS = JobStore()


async def comfy_get(client, url, *, job=None, **kwargs):
    """Retry safe reads only. Never resubmit a generation or upload."""
    for attempt in range(3):
        try:
            return await client.get(url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if attempt == 2:
                raise
            if job:
                job.detail = "Reconnecting to the image worker…"
            await asyncio.sleep(attempt + 1)


class ProgressMonitor:
    def __init__(self, url, workflow, job):
        self.url, self.workflow, self.job = url, workflow, job
        self.client_id = uuid.uuid4().hex
        self.prompt_id = None
        self.node = None
        self.pending = []
        self.ready = asyncio.Event()
        self.connected = False
        self.task = None

    async def __aenter__(self):
        if self.job:
            self.task = asyncio.create_task(self.watch())
            # Subscribe before submission so fast or cached runs aren't missed.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.ready.wait(), 3)
        return self

    async def __aexit__(self, *args):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    def bind(self, prompt_id):
        self.prompt_id = prompt_id
        if self.job:
            self.job.prompt_id = prompt_id
            self.job.update("queued", "Queued on the image worker")
            for event in self.pending:
                self.handle(event)
            self.pending.clear()

    def node_type(self, data):
        node = str(data.get("display_node", data.get("node", self.node)) or "")
        self.node = node
        return self.workflow.get(node, self.workflow.get(node.split(":")[0], {})).get("class_type", "")

    def handle(self, event):
        if not self.job or self.job.state in TERMINAL:
            return
        data = event.get("data", {})
        kind = event.get("type")
        if not isinstance(data, dict) or kind not in {
            "execution_start", "executing", "progress", "execution_success",
            "execution_error", "execution_interrupted", "executed"
        }:
            return
        if self.prompt_id is None:
            if len(self.pending) < 256:
                self.pending.append(event)
            return
        if data.get("prompt_id", self.prompt_id) != self.prompt_id:
            return
        if kind == "executed":
            if str(data.get("node")) == "900":
                preview = data.get("output", {}).get("text", [])
                if preview and isinstance(preview[0], str):
                    self.job.expanded_prompt = preview[0]
            return
        if kind == "execution_start":
            self.job.update("preparing", "Preparing models and references")
        elif kind == "executing":
            if data.get("node") is None:
                self.job.update("saving", "Retrieving the finished image")
                return
            node_type = self.node_type(data)
            if node_type == "TextGenerate":
                self.job.update("prompt", "Expanding your description",
                                detail="This optional step may take several minutes.")
            elif "Sampler" in node_type or node_type == "SamplerCustom":
                self.job.update("sampling", "Loading the image model and generating")
            elif "VAEDecode" in node_type:
                self.job.update("decoding", "Decoding the image")
            elif node_type in {"SaveImage", "PreviewImage"}:
                self.job.update("saving", "Saving the image")
            elif "TextEncode" in node_type or "CLIPTextEncode" in node_type:
                self.job.update("encoding", "Encoding the description and references")
            else:
                self.job.update("preparing", "Preparing models and references")
        elif kind == "progress":
            node_type = self.node_type(data)
            value, maximum = data.get("value"), data.get("max")
            if not isinstance(value, (int, float)) or not isinstance(maximum, (int, float)) or maximum <= 0:
                return
            if "Sampler" in node_type or self.job.stage == "sampling":
                percent = round(max(0, min(100, 100 * value / maximum)), 1)
                self.job.update("sampling", "Generating the image", percent,
                                f"Step {int(value)} of {int(maximum)} · {percent:g}%")
            elif node_type == "TextGenerate" or self.job.stage == "prompt":
                # max is a token ceiling, not a known output length.
                self.job.update("prompt", "Expanding your description",
                                detail=f"{int(value)} tokens generated · this step may take several minutes.")
        elif kind == "execution_success":
            self.job.update("saving", "Retrieving the finished image")
        elif kind in {"execution_error", "execution_interrupted"}:
            # History remains the authority for success/failure and persistence.
            self.job.update("checking", "Checking the image worker result")

    async def watch(self):
        parsed = urlsplit(self.url)
        uri = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                          parsed.path.rstrip("/") + "/ws", urlencode({"clientId": self.client_id}), ""))
        while True:
            try:
                async with connect(uri, proxy=None, open_timeout=2, close_timeout=1,
                                   max_size=2 * 1024 * 1024) as socket:
                    self.connected = True
                    self.ready.set()
                    async for message in socket:
                        if isinstance(message, str):
                            try:
                                self.handle(json.loads(message))
                            except (ValueError, TypeError, AttributeError):
                                LOG.debug("Ignoring malformed progress message")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                self.ready.set()
                if self.job.state in TERMINAL:
                    return
                self.job.percent = None
                self.job.detail = "Reconnecting to live progress; generation continues."
                await asyncio.sleep(2)


def progress_markup():
    return '''<link rel="stylesheet" href="/generation-assets/progress.css">
<section id="generation-progress" hidden aria-labelledby="generation-label">
<div class="progress-heading"><strong id="generation-label">Preparing…</strong><span id="generation-time">0:00</span></div>
<progress id="generation-bar" max="100" aria-label="Image generation progress"></progress>
<p id="generation-detail" class="muted"></p><p id="generation-message" role="status" aria-live="polite"></p>
<a id="generation-result-link" hidden>Open generated image</a>
<img id="generation-result" hidden alt="Generated image">
</section><script src="/generation-assets/progress.js" defer></script>'''


def install_progress_routes(app, current_user):
    @app.get("/generation-assets/{name}")
    async def progress_asset(name: str):
        mime = {"progress.js": "text/javascript", "progress.css": "text/css"}.get(name)
        if not mime:
            return Response(status_code=404)
        return Response(Path(__file__).with_name(name).read_text(), media_type=mime,
                        headers={"Cache-Control": "no-cache"})

    @app.get("/generation-jobs/active")
    async def active_job(request: Request):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "Sign in again to view progress."}, status_code=401)
        job = JOBS.active(user["id"])
        return JSONResponse({"job": job.snapshot() if job else None}, headers={"Cache-Control": "no-store"})

    @app.get("/generation-jobs/{job_id}")
    async def job_status(request: Request, job_id: str):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "Sign in again to view progress."}, status_code=401)
        job = JOBS.get(job_id, user["id"])
        if not job:
            return JSONResponse({"error": "Progress is no longer available. Check your gallery before starting again."}, status_code=404)
        return JSONResponse({"job": job.snapshot()}, headers={"Cache-Control": "no-store"})
