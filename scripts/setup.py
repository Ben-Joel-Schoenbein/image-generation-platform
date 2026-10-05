#!/usr/bin/env python3
"""Prepare the current Image Studio checkout, models and private configuration."""
import argparse
import ast
import configparser
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
RESERVE = 20 * 1024**3
MODEL_KEYS = {"unet_name": "diffusion_models", "clip_name": "text_encoders", "vae_name": "vae", "ckpt_name": "checkpoints", "lora_name": "loras"}


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def atomic_write(path, raw, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".studio-setup-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream: stream.write(raw)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def safe_path(root, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise ValueError("Unsafe setup destination")
    result = root / value
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Setup destination points outside the project")
    return result


def load_manifest():
    manifest = json.loads((HERE / "model-manifest.json").read_text())
    if manifest.get("version") != 1 or not manifest.get("files"):
        raise ValueError("Invalid model manifest")
    seen = set()
    for item in manifest["files"]:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", item.get("repo", "")) or not re.fullmatch(r"[a-f0-9]{40}", item.get("revision", "")):
            raise ValueError("Invalid model source")
        for name in [item["file"], item["destination"]]:
            if Path(name).is_absolute() or ".." in Path(name).parts: raise ValueError("Unsafe manifest filename")
        if not item["destination"].startswith("models/") or item["destination"] in seen:
            raise ValueError("Invalid model destination")
        seen.add(item["destination"])
        if not isinstance(item.get("size"), int) or item["size"] <= 0: raise ValueError("Invalid model size")
        if not isinstance(item.get("optional", False), bool): raise ValueError("Invalid optional model flag")
        if item.get("optional") and item.get("model") != "rapid_aio_v23_nsfw":
            raise ValueError("Unknown optional model")
        if not (re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")) or re.fullmatch(r"[a-f0-9]{40}", item.get("git_blob", ""))):
            raise ValueError("Missing model checksum")
    return manifest


def selected_manifest(manifest, include_rapid_v23=False):
    """The existing installation stays unchanged unless v23 is requested."""
    return {**manifest, "files": [item for item in manifest["files"]
            if not item.get("optional", False) or include_rapid_v23]}


def rapid_v23_manifest(manifest):
    files = [item for item in manifest["files"] if item.get("model") == "rapid_aio_v23_nsfw"]
    if len(files) != 1:
        raise ValueError("Rapid AIO v23 NSFW is missing from the setup manifest")
    return {**manifest, "files": files}


def project_check(root, manifest):
    required = ["compose.yaml", ".env.example", "compose.heretic.yaml", "prompt-enhancer/models.ini", "web/heretic_client.py", "web/rapid_aio.py", "web/Dockerfile", "comfyui/Dockerfile"]
    for name in required:
        if not safe_path(root, name).is_file(): raise ValueError("Required current repository file missing: " + name)
    known = {item["destination"] for item in manifest["files"]}
    for name in ["text2img.api.json", "img2img.api.json"]:
        workflow = json.loads((root / "workflows" / name).read_text())
        for node in workflow.values():
            for key, folder in MODEL_KEYS.items():
                filename = node.get("inputs", {}).get(key)
                if isinstance(filename, str) and "models/" + folder + "/" + filename not in known:
                    raise ValueError("Workflow uses a model not covered by this setup: " + filename)
    source = ast.parse((root / "web/rapid_aio.py").read_text())
    model = next((node.value.value for node in source.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and any(isinstance(t, ast.Name) and t.id == "MODEL_FILE" for t in node.targets)), None)
    if "models/checkpoints/" + str(model) not in known: raise ValueError("Rapid checkpoint differs from the setup manifest")
    variants = next((ast.literal_eval(node.value) for node in source.body
                     if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "RAPID_MODELS" for t in node.targets)), None)
    if not isinstance(variants, dict) or not variants:
        raise ValueError("Rapid checkpoint variants are missing")
    for filename in variants.values():
        if "models/checkpoints/" + filename not in known:
            raise ValueError("Rapid checkpoint variant differs from the setup manifest: " + filename)
    preset = configparser.ConfigParser(interpolation=None)
    preset.read_string("[global]\n" + (root/"prompt-enhancer/models.ini").read_text())
    for kind in ["pe-t2i", "pe-i2i"]:
        for key in ["model"] + (["mmproj"] if kind == "pe-i2i" else []):
            value = preset.get(kind, key, fallback="")
            if not value.startswith("/models/") or "models/prompt_enhancers/" + value.removeprefix("/models/") not in known:
                raise ValueError("Heretic preset uses an unknown model: " + kind + "." + key)
    for kind in ["t2i", "i2i"]:
        for name in ["system_prompt.txt", "LICENSE"]: safe_path(root, f"web/heretic_prompts/{kind}/{name}")
    for item in manifest["files"]: safe_path(root, item["destination"])


def read_env(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not match: raise ValueError("Unsupported .env syntax; use one KEY=value per line")
        key, value = match.groups()
        if key in result: raise ValueError("Duplicate .env key: " + key)
        if value.startswith("'"):
            m = re.fullmatch(r"'((?:[^'\\]|\\.)*)'\s*(?:#.*)?", value)
            if not m: raise ValueError("Invalid quoted .env value for " + key)
            value = m[1].replace("\\'", "'")
        elif value.startswith('"'):
            try: value = json.JSONDecoder().raw_decode(value)[0]
            except ValueError: raise ValueError("Invalid quoted .env value for " + key)
        else: value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        result[key] = value
    return result


def env_value(value):
    if "\n" in value or "\r" in value: raise ValueError("Multiline .env values are not supported")
    if "\\" in value: raise ValueError("New .env values must not contain backslashes; use a different password")
    return "'" + value.replace("'", "\\'") + "'"


def update_env(text, changes):
    remaining = dict(changes)
    lines = []
    for line in text.splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match[1] in remaining: line = match[1] + "=" + env_value(remaining.pop(match[1]))
        lines.append(line)
    lines.extend(key + "=" + env_value(value) for key, value in remaining.items())
    return "\n".join(lines) + "\n"


def placeholder(value):
    return not value or any(term in value.lower() for term in ["replace-with", "change-this", "paste_the_hash", "your-discord-application", "example.com"])


def validate_domain(value):
    labels = value.split(".")
    if len(labels) < 2 or len(value) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) for part in labels):
        raise ValueError("Set a real DNS hostname without https:// or a path")
    return value.lower()


def docker_command():
    if not shutil.which("docker"): raise ValueError("Install Docker Engine and its Compose plugin first")
    choices = [["docker"]]
    if shutil.which("sudo"): choices.append(["sudo", "-n", "docker"])
    for choice in choices:
        if subprocess.run(choice + ["info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            run(choice + ["compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return choice
    raise ValueError("Docker is unavailable. Start Docker and run sudo -v if sudo permission is needed")


def hash_password(docker, password):
    # Caddy reads stdin; no plaintext password appears in the process arguments.
    result = run(docker + ["run", "--rm", "-i", "caddy:2-alpine", "caddy", "hash-password", "--algorithm", "bcrypt"], input=password, text=True, capture_output=True)
    hashes = re.findall(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", result.stdout)
    if len(hashes) != 1: raise ValueError("Caddy did not return a valid password hash")
    return hashes[0]


def prepare_env(root, args, docker):
    env_path = safe_path(root, ".env")
    text = env_path.read_text() if env_path.exists() else (root / ".env.example").read_text()
    values, changes, credentials = read_env(text), {}, []
    if (root/"data/studio.sqlite3").exists() and (not env_path.exists() or placeholder(values.get("ADMIN_PASSWORD", ""))):
        raise ValueError("Existing generator data detected. Restore the original .env before setting up the server")
    def set_value(key, value): changes[key] = value; values[key] = value
    for key, supplied, label in [("DOMAIN", args.domain, "Domain der Bildgenerierung"), ("LIBRARY_DOMAIN", args.library_domain, "Domain des Archivs")]:
        value = supplied or values.get(key, "")
        if placeholder(value):
            if args.non_interactive: raise ValueError("Supply --domain and --library-domain, or a configured .env")
            value = input(label + " (nur Hostname): ").strip()
        validated = validate_domain(value)
        if validated != values.get(key): set_value(key, validated)
    if values["DOMAIN"] == values["LIBRARY_DOMAIN"]: raise ValueError("Generator and archive need different hostnames")
    if placeholder(values.get("IMAGE_STUDIO_URL", "")) or (args.domain and values.get("IMAGE_STUDIO_URL") != "https://" + values["DOMAIN"]):
        set_value("IMAGE_STUDIO_URL", "https://" + values["DOMAIN"])
    for key, default in [("ADMIN_USERNAME", "admin"), ("LIBRARY_AUTH_USER", "friends"), ("POSTGRES_USER", "image_library"), ("POSTGRES_DB", "image_library")]:
        if placeholder(values.get(key, "")): set_value(key, default)
    if placeholder(values.get("ADMIN_PASSWORD", "")):
        value = "" if args.non_interactive else getpass.getpass("Admin-Passwort (Enter = sicher erzeugen): ")
        value = value or secrets.token_urlsafe(24)
        if len(value) < 14: raise ValueError("Admin password needs at least 14 characters")
        set_value("ADMIN_PASSWORD", value); credentials.append("Generator: " + values["ADMIN_USERNAME"] + "\nPasswort: " + value)
    if len(values.get("ADMIN_PASSWORD", "")) < 14: raise ValueError("ADMIN_PASSWORD is too short")
    for key in ["SESSION_SECRET", "MEDIA_PROXY_SECRET", "POSTGRES_PASSWORD", "DISCORD_BOT_SECRET"]:
        if placeholder(values.get(key, "")): set_value(key, secrets.token_hex(32))
        if len(values[key]) < 32: raise ValueError(key + " is too short")
    if placeholder(values.get("LIBRARY_AUTH_HASH", "")):
        value = "" if args.non_interactive else getpass.getpass("Gemeinsames Archiv-Passwort (Enter = sicher erzeugen): ")
        value = value or secrets.token_urlsafe(24)
        set_value("LIBRARY_AUTH_HASH", hash_password(docker, value))
        credentials.append("Archiv: " + values["LIBRARY_AUTH_USER"] + "\nPasswort: " + value)
    if not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", values["LIBRARY_AUTH_HASH"]):
        raise ValueError("LIBRARY_AUTH_HASH must be a valid Caddy bcrypt hash")
    if placeholder(values.get("DISCORD_TOKEN", "")):
        value = "" if args.non_interactive else getpass.getpass("Discord-Bot-Token (optional, Enter = ohne Bot): ")
        set_value("DISCORD_TOKEN", value.strip())
    profiles = [value.strip() for value in values.get("COMPOSE_PROFILES", "").split(",") if value.strip()]
    if values.get("DISCORD_TOKEN") and "discord" not in profiles: profiles.append("discord")
    if not values.get("DISCORD_TOKEN"): profiles = [value for value in profiles if value != "discord"]
    if ",".join(profiles) != values.get("COMPOSE_PROFILES"): set_value("COMPOSE_PROFILES", ",".join(profiles))
    separator = values.get("COMPOSE_PATH_SEPARATOR", ":")
    if separator != ":": raise ValueError("This Linux setup requires COMPOSE_PATH_SEPARATOR=':'")
    files = values.get("COMPOSE_FILE", "").split(":") if values.get("COMPOSE_FILE") else ["compose.yaml"]
    overrides = [name for name in ["compose.override.yaml", "compose.override.yml"] if (root / name).is_file()]
    if len(overrides) > 1: raise ValueError("Keep only one Compose override file")
    if not values.get("COMPOSE_FILE"): files.extend(overrides)
    if "compose.heretic.yaml" not in files: files.append("compose.heretic.yaml")
    for name in files:
        if not safe_path(root, name).is_file(): raise ValueError("Compose file missing: " + name)
    if ":".join(files) != values.get("COMPOSE_FILE"): set_value("COMPOSE_FILE", ":".join(files))
    generated = update_env(text, changes)
    return text, generated, values, credentials, files


def compose_config(docker, root, env_text, files):
    fd, temporary = tempfile.mkstemp(prefix=".studio-setup-env-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream: stream.write(env_text)
        command = docker + ["compose", "--env-file", temporary]
        for name in files: command.extend(["-f", name])
        result = subprocess.run(command + ["config", "--format", "json"], cwd=root, capture_output=True, text=True)
        if result.returncode: raise ValueError("Compose configuration failed validation; check your .env and Compose files")
        config = json.loads(result.stdout)
        services = config.get("services", {})
        if "prompt-enhancer" not in services: raise ValueError("Heretic service is missing from Compose configuration")
        for service, target, source in [("comfyui", "/opt/ComfyUI/models", root/"models"), ("prompt-enhancer", "/models", root/"models/prompt_enhancers"), ("prompt-enhancer", "/config/models.ini", root/"prompt-enhancer/models.ini")]:
            mount = next((v for v in services[service].get("volumes", []) if v.get("target") == target), None)
            if not mount or mount.get("type") != "bind" or Path(mount["source"]).resolve() != source.resolve():
                raise ValueError("Custom model mount not supported by this setup: " + service)
        return config
    finally: Path(temporary).unlink(missing_ok=True)


def matches(path, item):
    if not path.is_file() or path.stat().st_size != item["size"]: return False
    checksum = hashlib.sha256() if "sha256" in item else hashlib.sha1()
    if "git_blob" in item: checksum.update(f"blob {item['size']}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""): checksum.update(chunk)
    return checksum.hexdigest() == item.get("sha256", item.get("git_blob"))


def protect_existing_database(docker, config, before, values):
    if read_env(before).get("POSTGRES_PASSWORD") == values.get("POSTGRES_PASSWORD"):
        return
    mounts = config.get("services", {}).get("media-db", {}).get("volumes", [])
    mount = next((v for v in mounts if v.get("type") == "volume" and v.get("target") == "/var/lib/postgresql/data"), None)
    if not mount: raise ValueError("Postgres data mount is not supported by this setup")
    volume = config.get("volumes", {}).get(mount["source"], {}).get("name")
    if not volume: raise ValueError("Cannot identify the Postgres data volume")
    existing = run(docker + ["volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True)
    if volume in existing.stdout.splitlines():
        raise ValueError("Existing archive database volume detected. Restore the original .env instead of generating a new database password")


def download_files(root, manifest):
    pending = []
    for item in manifest["files"]:
        target = safe_path(root, item["destination"])
        if target.exists():
            print("Prüfe vorhandene Datei:", target.name, flush=True)
            if not matches(target, item): raise ValueError("Existing file differs; not overwritten: " + str(target))
        else: pending.append((target, item))
    needed = 0
    for target, item in pending:
        partial = target.with_name(target.name + ".part")
        if partial.is_symlink(): raise ValueError("Unsafe partial download symlink")
        size = partial.stat().st_size if partial.exists() else 0
        if size > item["size"]: raise ValueError("Partial file is too large; move it aside: " + str(partial))
        needed += item["size"] - size
    if shutil.disk_usage(root).free < needed + RESERVE:
        raise ValueError(f"Need about {(needed+RESERVE)/1e9:.1f} GB free, including Docker build reserve")
    for target, item in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        if not matches(partial, item):
            if partial.exists() and partial.stat().st_size == item["size"]:
                raise ValueError("Completed partial file has wrong checksum; move it aside: " + str(partial))
            url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{quote(item['file'], safe='/')}"
            print(f"Download: {target.name} ({item['size']/1e9:.2f} GB; Fortsetzen möglich)", flush=True)
            run(["curl", "--fail", "--location", "--retry", "4", "--connect-timeout", "30", "--continue-at", "-", "--output", str(partial), url])
        if not matches(partial, item): raise ValueError("Checksum mismatch; move partial file aside and retry: " + str(partial))
        os.replace(partial, target)


def install_prompts(root):
    changes = []
    for kind in ["t2i", "i2i"]:
        for name in ["system_prompt.txt", "LICENSE"]:
            source = safe_path(root, f"models/prompt_enhancers/{kind}/{name}")
            raw = source.read_bytes()
            if name == "system_prompt.txt" and (len(raw) < 1000 or b"rewritten_prompt" not in raw): raise ValueError("Unexpected Heretic system prompt")
            target = safe_path(root, f"web/heretic_prompts/{kind}/{name}")
            if target.exists() and target.read_bytes() != raw: raise ValueError("Local Heretic prompt differs; not overwritten: " + str(target))
            if not target.exists(): changes.append((target, raw))
    for target, raw in changes: atomic_write(target, raw)


def save_configuration(root, before, after, credentials):
    path = safe_path(root, ".env")
    if path.exists() and path.read_text() != before: raise ValueError(".env changed during setup; not overwritten")
    credentials_path = safe_path(root, "setup-credentials.txt")
    if credentials and credentials_path.exists(): raise ValueError("setup-credentials.txt already exists; move it aside before generating new passwords")
    ignore_path = safe_path(root, ".gitignore")
    ignore = ignore_path.read_text() if ignore_path.exists() else ""
    for name in ["/.env", "/.env.setup-backup-*", "/setup-credentials.txt", "/web/heretic_prompts/", "/models/"]:
        if name not in ignore.splitlines(): ignore = ignore.rstrip() + "\n" + name + "\n"
    atomic_write(ignore_path, ignore.encode())
    if path.exists() and before != after:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        atomic_write(path.with_name(".env.setup-backup-"+stamp), before.encode(), 0o600)
    if credentials: atomic_write(credentials_path, ("\n\n".join(credentials)+"\n").encode(), 0o600)
    if not path.exists() or before != after: atomic_write(path, after.encode(), 0o600)
    else: path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--domain")
    parser.add_argument("--library-domain")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--check", action="store_true", help="Offline project/model plan only; no writes or downloads")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--rapid-v23", action="store_true", help="Also download Rapid AIO v23 NSFW (28.4 GB)")
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    manifest = load_manifest()
    project_check(root, manifest)
    manifest = selected_manifest(manifest, args.rapid_v23)
    print(f"Aktueller Modellbedarf: {len(manifest['files'])} Dateien, {sum(item['size'] for item in manifest['files'])/1e9:.1f} GB.", flush=True)
    if not args.rapid_v23:
        print("Rapid AIO v23 NSFW ist optional: --rapid-v23 oder python3 scripts/download-rapid-v23.py")
    if args.check:
        for item in manifest["files"]:
            state = "vorhanden (Prüfsumme wird beim Setup geprüft)" if (root/item["destination"]).exists() else "fehlt"
            print(item["destination"], "—", state)
        print("Offline-Vorabprüfung OK. Keine Dateien geändert.")
        return
    for program in ["curl", "nvidia-smi"]:
        if not shutil.which(program): raise ValueError("Missing " + program + "; prepare Docker/GPU driver/Container Toolkit first")
    docker = docker_command()
    for key in ["COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PATH_SEPARATOR"]:
        if key in os.environ: raise ValueError("Unset shell variable " + key + " so the project .env can control Compose")
    run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    before, after, values, credentials, files = prepare_env(root, args, docker)
    if credentials and (root/"setup-credentials.txt").exists(): raise ValueError("Move existing setup-credentials.txt aside before generating new credentials")
    # GPU compatibility is checked using the same CUDA/PyTorch base as the build.
    config = compose_config(docker, root, after, files)
    protect_existing_database(docker, config, before, values)
    build = config["services"]["comfyui"].get("build", {})
    image = build.get("args", {}).get("PYTORCH_BASE", values.get("PYTORCH_BASE", "pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime"))
    print("Prüfe Docker-GPU-Zugriff mit dem verwendeten PyTorch-Image …", flush=True)
    run(docker + ["run", "--rm", "--gpus", "all", "--entrypoint", "python", image, "-c", "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.cuda.get_device_name(0))"])
    download_files(root, manifest)
    install_prompts(root)
    save_configuration(root, before, after, credentials)
    if not args.no_build:
        print("Baue die Container …", flush=True)
        run(docker + ["compose", "build"], cwd=root)
        run(docker + ["compose", "pull", "prompt-enhancer", "caddy", "media-db"], cwd=root)
    print("Setup fertig. Vorhandene Daten wurden nicht geändert.")
    if credentials: print("Neue Zugangsdaten:", root/"setup-credentials.txt", "(nur für deinen Benutzer lesbar)")
    print("Jetzt im Projektordner starten: sudo docker compose up -d")


if __name__ == "__main__":
    try: main()
    except (ValueError, OSError, subprocess.CalledProcessError, configparser.Error) as error:
        # Captured subprocess errors intentionally do not print secret-bearing output.
        message = "Ein benötigter Docker-/Download-Befehl ist fehlgeschlagen." if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit("Setup abgebrochen: " + message)
