#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo 'Python 3 fehlt. Installiere python3 auf dem Server.' >&2; exit 1; }
exec python3 "$script_dir/setup.py" "$@"
