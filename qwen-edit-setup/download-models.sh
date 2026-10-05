#!/usr/bin/env bash
set -euo pipefail

# Run from the Friends Image Studio repository root, without sudo.
if [[ ! -f compose.yaml || ! -d workflows ]]; then
  echo 'Bitte im Projektordner mit compose.yaml und workflows/ starten.' >&2
  exit 1
fi
command -v curl >/dev/null || { echo 'curl fehlt: sudo apt install curl' >&2; exit 1; }
command -v sha256sum >/dev/null || { echo 'sha256sum fehlt.' >&2; exit 1; }

check_hash() {
  local file_path="$1" expected_hash="$2"
  local actual_hash
  actual_hash="$(sha256sum "$file_path")"
  [[ "${actual_hash%% *}" == "$expected_hash" ]]
}

download() {
  local dest="$1" url="$2" expected_hash="$3"
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]]; then
    echo "Prüfe vorhandene Datei: $dest"
    if check_hash "$dest" "$expected_hash"; then
      echo 'Bereits vollständig vorhanden.'
      return
    fi
    echo "Prüfsumme stimmt nicht. Datei prüfen oder umbenennen: $dest" >&2
    exit 1
  fi
  echo "Lade herunter: $dest"
  curl --fail --location --retry 3 --continue-at - --output "$dest.part" "$url"
  echo 'Prüfe SHA-256; bei großen Dateien dauert das etwas.'
  if ! check_hash "$dest.part" "$expected_hash"; then
    echo "Download-Prüfsumme stimmt nicht: $dest.part" >&2
    echo 'Prüfe die Quelle. Für einen neuen Download die .part-Datei umbenennen.' >&2
    exit 1
  fi
  mv "$dest.part" "$dest"
}

download \
  'models/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors' \
  'https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors' \
  '318568f61951ab9da21100c7b896e3c1da67f0d2efad6421545e022cfaa2b2b4'

download \
  'models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors' \
  'https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors' \
  'cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4'

download \
  'models/vae/qwen_image_vae.safetensors' \
  'https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors' \
  'a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f'

download \
  'models/loras/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors' \
  'https://huggingface.co/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors' \
  '2a32ce938ec71db2b49a817b4844ae86995569518dea56ee0ddc209cbe8e1377'

echo 'Alle vier Modelldateien sind vollständig und geprüft.'
