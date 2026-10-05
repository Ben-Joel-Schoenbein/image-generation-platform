#!/usr/bin/env python3
"""Check the installed API workflow against the running ComfyUI schema.

Run in the web container using stdin; this does not queue a generation.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else '/workflows/img2img.api.json')
    base_url = os.environ.get('COMFY_URL', 'http://comfyui:8188').rstrip('/')
    try:
        workflow = json.loads(path.read_text())
        with urllib.request.urlopen(base_url + '/object_info', timeout=30) as response:
            schema = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f'Prüfung fehlgeschlagen: {exc}', file=sys.stderr)
        return 1

    errors = []
    for node_id, node in workflow.items():
        kind = node.get('class_type')
        if kind not in schema:
            errors.append(f'Knoten {node_id}: {kind} fehlt. ComfyUI aktualisieren; Start-Logs prüfen.')
            continue
        metadata = schema[kind]
        inputs = node.get('inputs', {})
        expected = metadata.get('input', {})
        for name, field in expected.get('required', {}).items():
            if name not in inputs:
                errors.append(f'Knoten {node_id} ({kind}): erforderliches Feld {name} fehlt.')

        for name, value in inputs.items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                upstream = workflow.get(value[0])
                if upstream is None:
                    errors.append(f'Knoten {node_id}: Verbindung verweist auf fehlenden Knoten {value[0]}.')
                    continue
                upstream_schema = schema.get(upstream['class_type'], {})
                outputs = upstream_schema.get('output', [])
                if not isinstance(value[1], int) or not 0 <= value[1] < len(outputs):
                    errors.append(f'Knoten {node_id}: ungültiger Ausgabeindex für {name}.')
                continue
            if isinstance(value, str) and '{{' in value:
                continue
            field = expected.get('required', {}).get(name) or expected.get('optional', {}).get(name)
            if field and isinstance(field[0], list) and value not in field[0]:
                errors.append(f'Knoten {node_id} ({kind}): {name}={value!r} ist nicht verfügbar. Modellpfad/Version prüfen.')

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print('Workflow-Knoten, Verbindungen und Modellnamen werden von ComfyUI erkannt.')
    print('GPU-Speicherbedarf und Bildergebnis werden erst bei einer Generierung geprüft.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
