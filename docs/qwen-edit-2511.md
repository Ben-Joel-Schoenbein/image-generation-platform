# Qwen Image Edit 2511: FP8 Mixed und BF16

Die beiden zusätzlichen Optionen verwenden separate Diffusionsmodelle, einen gemeinsamen Qwen-2.5-VL-FP8-Encoder und den Qwen-Image-VAE. Rapid AIO v19/v23 NSFW und Qwen Image 2.1 bleiben verfügbar. Die neuen 2511-Dateien sind reguläre Basismodelle ohne ausdrückliche NSFW-Spezialisierung.

## Auf dem bestehenden Server

Nach Übernahme der Integrationsdateien:

```bash
cd ~/image-studio
python3 scripts/download-qwen-edit-2511.py --precision both --check
python3 scripts/download-qwen-edit-2511.py --precision both
bash studio-compose.sh up -d --build --no-deps --force-recreate comfyui web discord
bash studio-compose.sh logs --tail=80 web discord
```

Nur FP8 installieren: `--precision fp8`. Nur BF16 installieren: `--precision bf16`. Bereits passende Dateien werden nach Größen-/Prüfsummenprüfung wiederverwendet; unterbrochene Downloads lassen sich mit demselben Befehl fortsetzen. Es werden weder `.env` noch Bilder oder Datenbanken geändert. Der Download reserviert zusätzlich 20 GiB freien Speicher.

| Auswahl | Diffusionsmodell | Gemeinsamer Encoder/VAE | Gesamter zusätzlicher Download |
| --- | --- | --- | --- |
| FP8 Mixed | 20,53 GB | 9,64 GB | 30,17 GB |
| BF16 | 40,86 GB | 9,64 GB | 50,50 GB |
| Beide | 61,39 GB | einmal 9,64 GB | 71,03 GB |

Dateien und feste Hugging-Face-Revisionen mit SHA-256 stehen in `scripts/model-manifest.json`. Die BF16-Auswahl bezieht sich auf das Diffusionsmodell; der gemeinsame Textencoder bleibt FP8. Auf der A40 mit 48 GB kann BF16 CPU-Offloading benötigen; ausreichendes RAM ist nötig. Mit FP8, 1K und 40 Schritten beginnen.

## Website und Discord

Im Modellfeld **Qwen Image Edit 2511 — FP8 Mixed** oder **— BF16** auswählen. Beide erlauben Textgenerierung und Bearbeitung mit maximal **drei Referenzbildern**. Das Ausgabeseitenverhältnis folgt dem ersten Referenzbild; ohne Referenz entsteht ein Quadrat. Die Qualität wählt 1K oder 2K. `edit_steps` bietet 20, 30 und 40 Schritte, Standard 40. `rapid_steps` gilt weiterhin nur für Rapid AIO.

Der Ablauf übernimmt die aktive Nicht-Lightning-Variante des offiziellen ComfyUI-Templates: Euler, Simple, CFG 4, AuraFlow-Shift 3,1 und CFGNorm. Die voreingestellten vier Rapid-Schritte und CFG 1 werden für diese Modelle nicht verwendet. Die Lightning-LoRA wird nicht automatisch heruntergeladen.

Die drei Optionen für Prompt-Erweiterung funktionieren mit beiden Präzisionen:

- **Off:** Eingabe direkt an das Bildmodell.
- **Standard:** bestehende Qwen-2.1-PE-Encoder und deren Systemprompts; die Bildgenerierung bleibt Edit 2511. Die tatsächliche Erweiterung erscheint in der Fortschrittsanzeige.
- **Heretic:** vorhandene separate GGUF-PE-Dienste vor der Bildgenerierung. Das Setup liefert diese bereits mit.

Für die beiden neuen Modelle ist Auto ohne ausdrückliches `enhance_prompt` standardmäßig Off. In Discord stehen `model`, `edit_steps` und `prompt_expansion` bei `/imagine` und `/edit`. Nach dem Bot-Neustart Discord neu laden und einen neuen Slash-Command beginnen.

LoRAs werden aus `models/loras/` erkannt. Stärke und erlaubte Einzelmodelle unter **Admin → LoRAs → Apply to models** einstellen. Jede Datei kann beispielsweise nur BF16, beide 2511-Präzisionen oder nur AIO v23 verwenden. Bestehende Familienzuordnungen behalten ihre Wirkung, bis du die Häkchen änderst. Details: [automatic-loras.md](automatic-loras.md).

## Neues Deployment

Nach Commit/Push aller Quelldateien auf einem neuen Server:

```bash
bash scripts/setup.sh --rapid-v23 --qwen-edit-2511 both
sudo docker compose up -d
```

Ohne `--qwen-edit-2511` lädt das Setup keine zusätzlichen 2511-Dateien. Mit `fp8` oder `bf16` lässt sich eine einzelne Variante wählen. Beide Varianten plus v23 ergeben zusammen mit dem vorhandenen Basisset rund **192 GB** Modelldateien. Docker-Images, Bilder und die Downloadreserve kommen hinzu.

## GPU-Vergleich auf deinem Server

Der Test benutzt die echte Job-API und erzeugt Bilder nacheinander mit gleicher Seed, Qualität, Schrittzahl und Eingabe. Er respektiert die gespeicherten LoRA-Einstellungen; für einen Vergleich ohne LoRAs diese vorher in der Adminseite deaktivieren.

```bash
bash studio-compose.sh exec -T web python - --precision both < scripts/test-qwen-edit-2511.py
bash studio-compose.sh cp web:/data/qwen-edit-2511-tests ~/qwen-edit-2511-tests
```

Alle drei Prompt-Erweiterungen mit beiden Varianten vergleichen:

```bash
bash studio-compose.sh exec -T web python - --precision both --all-expansions < scripts/test-qwen-edit-2511.py
```

Die Bilder und JSON-Dateien enthalten Modell, Laufzeit, Schritte, Seed und gegebenenfalls den erweiterten Prompt. `--prompt`, `--quality high`, `--steps 20`, `--seed` und `--prompt-expansion` sind optional. Für Bildbearbeitung bis zu drei `--reference /container/pfad.png` angeben; die Dateien müssen im Web-Container erreichbar sein. Das Script erwartet einen freien Worker und bricht bei API-/Generierungsfehlern ab.

## Quellen und Prüfgrenze

- [Offizielles ComfyUI-2511-Template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_edit_2511.json)
- [Offizielle Diffusionsmodelle](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/tree/main/split_files/diffusion_models)
- [Encoder und VAE](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/tree/main/split_files)

Automatisierte Prüfungen decken Workflow, API, Website, echte Discord-Registrierung, Prompt-Erweiterung, LoRA-Zuordnung und Downloadauswahl ab. Ein echter CUDA-Lauf und die Bildqualität werden mit dem obigen Script auf dem Server geprüft; sie wurden in der Entwicklungsumgebung nicht ausgeführt.
