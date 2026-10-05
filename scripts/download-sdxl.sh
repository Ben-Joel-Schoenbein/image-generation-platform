#!/usr/bin/env bash
set -euo pipefail

[[ -f compose.yaml ]] || { echo 'Bitte im Projektordner mit compose.yaml starten.' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl fehlt: sudo apt install curl' >&2; exit 1; }
model_file='models/checkpoints/sd_xl_base_1.0.safetensors'
model_hash='31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b'
model_url='https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors'
mkdir -p models/checkpoints
check_model() {
  local actual_hash
  actual_hash="$(sha256sum "$1")"
  [[ "${actual_hash%% *}" == "$model_hash" ]]
}
if [[ -f "$model_file" ]]; then
  check_model "$model_file" || { echo "Falsche Prüfsumme: $model_file. Datei prüfen oder umbenennen." >&2; exit 1; }
  echo 'SDXL ist bereits vollständig vorhanden.'
  exit 0
fi
echo 'Lade SDXL Base für den Textmodus (ungefähr 6,94 GB).'
curl --fail --location --retry 3 --continue-at - --output "$model_file.part" "$model_url"
check_model "$model_file.part" || { echo 'Prüfsumme falsch. Download wurde nicht als Modell installiert.' >&2; exit 1; }
mv "$model_file.part" "$model_file"
echo 'SDXL vollständig geladen und geprüft.'
