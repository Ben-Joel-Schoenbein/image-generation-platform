# Update: bis zu zehn Referenzbilder

Website und Discord-Befehl `/edit` nehmen 1–10 PNG-, JPEG- oder WebP-Dateien an.
Die Website zeigt die Dateireihenfolge als „Image 1“, „Image 2“, usw. an. Beim
Discord-Befehl ist `reference` das erste Bild; `reference2` bis `reference10`
sind optional. Lass beim Nummerieren möglichst keine Lücken. Im Prompt kannst
du beispielsweise schreiben: „Combine the characters from Image 1, Image 2
and Image 3 in the setting from Image 4.“

Alle Bilder werden einzeln an den Textencoder und als Referenz-Latents an Qwen
übergeben. Es wird keine Collage erstellt. Der eigene ComfyUI-Knoten
`StudioQwenImageEditReferences` erweitert die drei Anschlüsse des nativen
Qwen-2509-Knotens auf zehn. Der Knoten wird mit dem ComfyUI-Dockerimage installiert.
Der erste Upload bestimmt das Seitenverhältnis und ungefähr eine Million Pixel
der Ausgabe. Für die Referenz-Latents verteilt der Workflow insgesamt ungefähr
drei Millionen Pixel auf alle Bilder; damit steigt die Tokenzahl nicht auf zehn
Referenzen mit je einer Million Pixel. Dies verkleinert Details bei vielen Bildern.
Die Bilder für den Vision-Encoder werden weiterhin einzeln verarbeitet.

Das Qwen-Modell ist für 1–3 Bilder optimiert. Die Erweiterung erlaubt zehn
Eingaben, aber garantiert weder die gleiche Motivtreue bei zehn Bildern noch
einen bestimmten VRAM-Verbrauch. Bildqualität, echte CUDA-Ausführung und Laufzeit
müssen auf der A40 getestet werden. Das Backend wartet wie bisher höchstens
420 Sekunden auf einen Auftrag; ein Timeout beendet einen laufenden ComfyUI-Auftrag
nicht automatisch.

## HTTP 400 und fehlende Modelle

Der bisherige Textworkflow erwartet SDXL Base, nicht Qwen Image Edit. Bei
ausschließlich heruntergeladenen Qwen-Gewichten fehlt deshalb möglicherweise
`models/checkpoints/sd_xl_base_1.0.safetensors`. Der neue Downloader lädt genau
diese Datei (ungefähr 6,94 GB), setzt Downloads fort und prüft die offizielle
SHA-256-Prüfsumme. Eine vorhandene Datei wird geprüft und nicht überschrieben.

Die App prüft Knoten, Modelle und Auswahlfelder gegen ComfyUIs `/object_info`
und zeigt bei HTTP 400 die konkrete Fehlerantwort. Ein Screenshot mit nur
„400 Bad Request“ beweist die genaue Ursache noch nicht; mit dem Update werden
auch weitere ungültige Eingaben sichtbar. Laufzeitfehler in `/history` werden
ebenfalls mit ComfyUIs eigentlicher Fehlermeldung angezeigt.

## Installation auf deinem Server

Nach dem Übernehmen dieser Änderungen in dein Git-Repository:

```bash
cd ~/image-studio
git status --short
GIT_SSH_COMMAND='ssh -i ~/.ssh/image-studio-deploy -o IdentitiesOnly=yes' git pull --ff-only
bash scripts/download-sdxl.sh
bash qwen-edit-setup/download-models.sh
sudo docker compose --profile discord build comfyui web discord
sudo docker compose --profile discord up -d
sudo docker compose exec -T web python - /workflows/text2img.api.json < qwen-edit-setup/check-workflow.py
sudo docker compose exec -T web python - /workflows/img2img.api.json < qwen-edit-setup/check-workflow.py
sudo docker compose --profile discord ps
```

Der Qwen-Downloader überspringt die bereits vorhandenen und korrekt geprüften
vier Dateien. Der ComfyUI-Neubau enthält `build-essential` für Triton sowie den
neuen Referenzknoten. Das Backend startet erst, wenn ComfyUI gesund ist. Datenbanken,
Accounts, Modelle und Bilder werden dabei nicht gelöscht.

Wenn `git status --short` Änderungen auf deinem Server zeigt, insbesondere die
manuell korrigierte `comfyui/Dockerfile`, sichere diese Änderungen vor `git pull`
und übernimm sie gezielt. Verwende keinen pauschalen Hard Reset.

In `.env` bleibt `DISCORD_TOKEN` dein Discord-Token und `DISCORD_BOT_SECRET`
der gemeinsame Schlüssel von Bot und Backend. Für die Referenzbilder gelten
standardmäßig 15 MB pro Bild und 60 MB zusammen. `MAX_UPLOAD_MB` und
`MAX_REFERENCE_TOTAL_MB` können diese Grenzen ändern.
Setze den vollständigen `LIBRARY_AUTH_HASH` in einfache Anführungszeichen,
damit Docker keine Hashbestandteile als Variablen ersetzt.

## Tests

```bash
python -m venv .venv
.venv/bin/pip install -r web/requirements.txt -r discord/requirements.txt pytest
.venv/bin/python -m pytest -q tests
```

Die Tests verwenden eine simulierte ComfyUI-API und Tensor-Schnittstelle; sie
prüfen Website-Uploads, Discord-Befehle, alle zehn Referenzanschlüsse,
Einzelbild-Kompatibilität, Uploadgrenzen, Authentifizierung und Fehlermeldungen.
Sie laden keine Modellgewichte und erzeugen keine Bilder auf einer GPU.

Quellen:
- Qwen-Modellkarte: https://huggingface.co/Qwen/Qwen-Image-Edit-2509
- Native Qwen-Knoten: https://github.com/Comfy-Org/ComfyUI/blob/f1072eb0350638a3390ddb6afbcaa8c6b237c6fd/comfy_extras/nodes_qwen.py
- SDXL Base: https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
