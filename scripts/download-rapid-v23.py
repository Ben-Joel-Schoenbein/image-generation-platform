#!/usr/bin/env python3
"""Download only Qwen Rapid AIO v23 NSFW into an existing Image Studio checkout."""
import argparse
import shutil
import subprocess
from pathlib import Path

import setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="Offline plan; no download or writes")
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    manifest = setup.load_manifest()
    setup.project_check(root, manifest)
    selected = setup.rapid_v23_manifest(manifest)
    item = selected["files"][0]
    target = root / item["destination"]
    print(f"Qwen Rapid AIO v23 NSFW: {item['size']/1e9:.2f} GB\nZiel: {target}", flush=True)
    if args.check:
        print("Vorhanden; Prüfsumme wird beim Download geprüft." if target.exists() else "Fehlt; Download erforderlich.")
        print("Offline-Prüfung OK. Keine Dateien geändert.")
        return
    if not shutil.which("curl"):
        raise ValueError("curl is required")
    setup.download_files(root, selected)
    print("Rapid AIO v23 NSFW ist installiert. Web und Discord mit studio-compose.sh neu bauen.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        message = "Download fehlgeschlagen; denselben Befehl zum Fortsetzen erneut ausführen." if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit("Rapid-AIO-Download abgebrochen: " + message)
