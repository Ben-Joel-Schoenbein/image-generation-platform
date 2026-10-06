#!/usr/bin/env python3
"""Download selected Edit 2511 diffusion weights plus their shared encoder and VAE."""
import argparse
import shutil
import subprocess
from pathlib import Path

import setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--precision", choices=("fp8", "bf16", "both"), default="both")
    parser.add_argument("--check", action="store_true", help="Offline plan; no writes or download")
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    manifest = setup.load_manifest()
    setup.project_check(root, manifest)
    selected = setup.qwen_edit_manifest(manifest, args.precision)
    print(f"Qwen Image Edit 2511 ({args.precision}): {sum(i['size'] for i in selected['files'])/1e9:.2f} GB insgesamt.", flush=True)
    for item in selected["files"]:
        path = root / item["destination"]
        print(f"{path} — {item['size']/1e9:.2f} GB — {'vorhanden' if path.exists() else 'fehlt'}", flush=True)
    if args.check:
        print("Offline-Prüfung OK. Keine Dateien geändert. Vorhandene Prüfsummen werden beim Download geprüft.")
        return
    if not shutil.which("curl"):
        raise ValueError("curl is required")
    setup.download_files(root, selected)
    print("Edit 2511 ist installiert. ComfyUI neu starten; Web/Discord nach dem Quellcode-Update neu bauen.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        message = "Download fehlgeschlagen; denselben Befehl zum Fortsetzen erneut ausführen." if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit("Edit-2511-Download abgebrochen: " + message)
