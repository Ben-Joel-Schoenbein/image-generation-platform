#!/usr/bin/env python3
"""Run inside the web container; generate a v23 test image through the normal job API."""
import argparse
import base64
import json
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "http://127.0.0.1:8000/internal/discord/jobs"


def api_request(url, secret, payload=None):
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read()
            return json.loads(raw) if response.headers.get_content_type() == "application/json" else raw
    except HTTPError as error:
        raise RuntimeError(f"Image Studio HTTP {error.code}: {error.read(2000).decode(errors='replace')}") from error


def run_test(model, args, secret, output):
    references = []
    for path in args.reference:
        references.append({"filename": path.name, "image_b64": base64.b64encode(path.read_bytes()).decode()})
    owner = "rapid-v23-test-" + output.name
    payload = {"discord_user_id": owner, "generation_job_id": uuid.uuid4().hex,
               "mode": "edit" if references else "text", "prompt": args.prompt,
               "references": references, "model": model, "quality": args.quality,
               "rapid_steps": args.steps, "seed": args.seed, "prompt_expansion": args.prompt_expansion}
    started = time.monotonic()
    job = api_request(API, secret, payload)["job"]
    url = API + "/" + job["id"] + "?discord_user_id=" + owner
    last = None
    while True:
        state = job["state"]
        message = (state, job.get("stage"), job.get("label"))
        if message != last:
            print(model, state, job.get("label", ""), flush=True)
            last = message
        if state == "done":
            image = api_request(API + "/" + job["id"] + "/image?discord_user_id=" + owner, secret)
            if not isinstance(image, bytes):
                raise RuntimeError("Image endpoint returned no image")
            output.mkdir(parents=True, exist_ok=True)
            path = output / (model + ".jpg")
            path.write_bytes(image)
            result = {"model": model, "seconds": round(time.monotonic() - started, 2),
                      "seed": args.seed, "steps": args.steps, "quality": args.quality,
                      "prompt_expansion": args.prompt_expansion, "prompt": args.prompt,
                      "references": [p.name for p in args.reference], "image": path.name,
                      "expanded_prompt": job.get("expanded_prompt")}
            path.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
            print("OK:", path, "—", result["seconds"], "Sekunden", flush=True)
            return
        if state in {"error", "failed", "cancelled"}:
            raise RuntimeError(job.get("error") or "Generation failed")
        if time.monotonic() - started > args.timeout:
            raise RuntimeError("Test timed out; check the website/worker before starting another test")
        time.sleep(2)
        job = api_request(url, secret)["job"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-v19", action="store_true")
    parser.add_argument("--prompt", default="A red vintage car parked beside a mountain lake, natural daylight, realistic photograph")
    parser.add_argument("--reference", type=Path, action="append", default=[], help="Reference path inside the web container; repeat up to four times")
    parser.add_argument("--quality", choices=["standard", "high"], default="standard")
    parser.add_argument("--steps", type=int, choices=[4, 6, 8], default=4)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--prompt-expansion", choices=["off", "standard", "heretic"], default="off")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if len(args.reference) > 4:
        raise ValueError("Rapid AIO supports at most four references")
    if not 0 <= args.seed < 2**32:
        raise ValueError("Seed must be between 0 and 4294967295")
    secret = os.environ.get("DISCORD_BOT_SECRET", "")
    if not secret:
        raise ValueError("Run this script inside the configured web container")
    status = api_request("http://127.0.0.1:8000/internal/generation-status", secret)
    if status.get("active_jobs", 0):
        raise RuntimeError("The image worker is busy; finish its current job before running the comparison")
    output = Path(os.environ.get("DATA_DIR", "/data")) / "rapid-v23-tests" / uuid.uuid4().hex
    for model in (["rapid_aio_v19"] if args.compare_v19 else []) + ["rapid_aio_v23_nsfw"]:
        run_test(model, args, secret, output)
    print("Ergebnisse aus dem Container kopieren: bash studio-compose.sh cp web:/data/rapid-v23-tests ~/rapid-v23-tests")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit("Rapid-AIO-Test fehlgeschlagen: " + str(error))
