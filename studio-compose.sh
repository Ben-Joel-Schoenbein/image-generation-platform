#!/usr/bin/env bash
# IMAGE_STUDIO_HERETIC_PE_V1
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$project_dir"
compose_files=(-f compose.yaml)
if [[ -f compose.override.yaml && -f compose.override.yml ]]; then
  echo "Two Compose override files found; retain only the override your project uses." >&2
  exit 1
fi
if [[ -f compose.override.yaml ]]; then compose_files+=(-f compose.override.yaml); fi
if [[ -f compose.override.yml ]]; then compose_files+=(-f compose.override.yml); fi
compose_files+=(-f compose.heretic.yaml)
exec sudo docker compose "${compose_files[@]}" --profile discord "$@"
