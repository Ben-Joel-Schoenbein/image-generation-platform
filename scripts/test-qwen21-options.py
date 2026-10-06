#!/usr/bin/env python3
"""Run inside the web container; compare Qwen 2.1 encoders and sampling settings through the normal job API."""
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


def run_test(encoder, args, secret, output):
    model = "qwen21"
    label = f"{encoder}-{args.sampler}-{args.scheduler}"
    references = []
    for path in args.reference:
        references.append({"filename": path.name, "image_b64": base64.b64encode(path.read_bytes()).decode()})
    owner = "qwen21-options-test-" + output.name
    payload = {"discord_user_id": owner, "generation_job_id": uuid.uuid4().hex,
               "mode": "edit" if references else "text", "prompt": args.prompt,
               "references": references, "model": model, "quality": args.quality,
               "qwen_sampler": args.sampler, "qwen_scheduler": args.scheduler,
               "qwen_text_encoder": encoder, "seed": args.seed, "prompt_expansion": args.prompt_expansion}
    started = time.monotonic()
    job = api_request(API, secret, payload)["job"]
    url = API + "/" + job["id"] + "?discord_user_id=" + owner
    last = None
    while True:
        state = job["state"]
        message = (state, job.get("stage"), job.get("label"))
        if message != last:
            print(label, state, job.get("label", ""), flush=True)
            last = message
        if state == "done":
            image = api_request(API + "/" + job["id"] + "/image?discord_user_id=" + owner, secret)
            if not isinstance(image, bytes):
                raise RuntimeError("Image endpoint returned no image")
            output.mkdir(parents=True, exist_ok=True)
            path = output / (label + ".jpg")
            path.write_bytes(image)
            result = {"model": model, "seconds": round(time.monotonic() - started, 2),
                      "seed": args.seed, "sampler": args.sampler, "scheduler": args.scheduler,
                      "text_encoder": encoder, "quality": args.quality,
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
    parser.add_argument("--encoder", choices=["bf16", "int8_convrot", "both"], default="both")
    parser.add_argument("--sampler", choices=["default", "euler", "er_sde"], default="er_sde")
    parser.add_argument("--scheduler", choices=["default", "simple", "beta"], default="beta")
    parser.add_argument("--compare-defaults", action="store_true", help="Also compare the original workflow settings")
    parser.add_argument("--all-expansions", action="store_true", help="Compare off, standard and heretic sequentially")
    parser.add_argument("--prompt", default="A red vintage car parked beside a mountain lake, natural daylight, realistic photograph")
    parser.add_argument("--reference", type=Path, action="append", default=[], help="Reference path inside the web container; repeat up to ten times")
    parser.add_argument("--quality", choices=["standard", "high"], default="standard")
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--prompt-expansion", choices=["off", "standard", "heretic"], default="off")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if len(args.reference) > 10:
        raise ValueError("Qwen 2.1 supports at most ten references")
    if not 0 <= args.seed < 2**32:
        raise ValueError("Seed must be between 0 and 4294967295")
    secret = os.environ.get("DISCORD_BOT_SECRET", "")
    if not secret:
        raise ValueError("Run this script inside the configured web container")
    status = api_request("http://127.0.0.1:8000/internal/generation-status", secret)
    if status.get("active_jobs", 0):
        raise RuntimeError("The image worker is busy; finish its current job before running the comparison")
    output = Path(os.environ.get("DATA_DIR", "/data")) / "qwen21-options-tests" / uuid.uuid4().hex
    encoders = ("bf16", "int8_convrot") if args.encoder == "both" else (args.encoder,)
    settings = [(args.sampler, args.scheduler)]
    if args.compare_defaults and settings[0] != ("default", "default"):
        settings.insert(0, ("default", "default"))
    expansions = ("off", "standard", "heretic") if args.all_expansions else (args.prompt_expansion,)
    for expansion in expansions:
        args.prompt_expansion = expansion
        for sampler, scheduler in settings:
            args.sampler, args.scheduler = sampler, scheduler
            for encoder in encoders:
                run_test(encoder, args, secret, output / expansion)
    print("Ergebnisse aus dem Container kopieren: bash studio-compose.sh cp web:/data/qwen21-options-tests ~/qwen21-options-tests")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit("Qwen-2.1-Test fehlgeschlagen: " + str(error))
