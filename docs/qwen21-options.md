# Qwen Image 2.1: ER-SDE, Beta und INT8 ConvRot

Website und Discord bieten drei unabhängige Optionen für **Qwen Image 2.1**:

| Option | Auswahl | Standard |
| --- | --- | --- |
| Sampler / `qwen_sampler` | `default`, `euler`, `er_sde` | Wert des installierten Workflows, derzeit Euler |
| Scheduler / `qwen_scheduler` | `default`, `simple`, `beta` | Wert des installierten Workflows, derzeit Simple |
| Textencoder / `qwen_text_encoder` | `bf16`, `int8_convrot` | BF16 |

Die Schrittzahl bleibt die des Qwen-2.1-Workflows, derzeit 40. Sampler und Scheduler benötigen keine zusätzlichen Gewichte. Der INT8-Encoder wird nur auf ausdrücklichen Wunsch heruntergeladen.

## Vorhandener Server

Nach dem Quellcode-Update:

```bash
cd ~/image-studio
python3 scripts/download-qwen21-encoder.py --check
python3 scripts/download-qwen21-encoder.py
bash studio-compose.sh up -d --build --no-deps --force-recreate comfyui web discord
bash studio-compose.sh up -d
bash studio-compose.sh ps -a
```

Der letzte Startbefehl startet auch Caddy und die Archivdienste. Web und ComfyUI haben keine öffentlichen Host-Ports; die Website benötigt Caddy.

Der Download umfasst exakt 10.985.825.432 Bytes, rund 11 GB, nach `models/text_encoders/qwen3vl_8b_int8_convrot.safetensors`. Das Script kann Downloads fortsetzen, prüft vorhandene Dateien anhand SHA-256 und reserviert zusätzlich 20 GiB freien Speicher. Ein bereits vorhandener BF16-Encoder bleibt erhalten. Keine Änderung an `.env`, Bildern oder Admin-LoRA-Einstellungen.

## Website und Discord

Auf der Generierungsseite **Qwen Image 2.1** auswählen. Unter **Qwen Image 2.1 settings** lassen sich **ER-SDE**, **Beta** und **Qwen3-VL 8B — INT8 ConvRot** wählen. Die Optionen werden für andere Modelle ausgeblendet und nicht übermittelt. Die gewählten Werte bleiben beim Wechsel zurück zu Qwen in derselben Seite erhalten.

In Discord einen neuen `/imagine`- oder `/edit`-Befehl beginnen:

```text
model: qwen21
qwen_sampler: er_sde
qwen_scheduler: beta
qwen_text_encoder: int8_convrot
```

Bei alten Befehlsoptionen Discord mit Ctrl+R neu laden. Der Bot synchronisiert die Befehle beim Start; `/edit` hat insgesamt 22 Optionen und bleibt unter Discords Grenze von 25.

Die Encoder-Auswahl ändert ausschließlich den Qwen3-VL-Bild/Text-Encoder im Generierungsworkflow. Der Standard-Prompt-Enhancer Qwen3.5 beziehungsweise der Heretic-Dienst bleiben separat. **Off, Standard und Heretic**, bis zu zehn Referenzbilder, 1K/2K, Seeds und die individuellen Qwen-2.1-LoRAs werden weiterhin unterstützt. Rapid AIO und Edit 2511 behalten ihre eigene Generierung; diese drei Optionen sind dort nicht anwendbar.

Fehlt der ausgewählte Encoder oder kennt der laufende ComfyUI-Container ER-SDE/Beta nicht, wird der Auftrag vor dem Einreihen in ComfyUI mit einer Fehlermeldung abgebrochen. Es gibt keinen stillen Wechsel auf andere Einstellungen.

## Setup auf einem neuen Server

Nach Übernahme des Quellcodes ins Repository:

```bash
bash scripts/setup.sh --qwen21-int8-encoder
sudo docker compose up -d
```

Kombinierbar mit den übrigen Downloadprofilen:

```bash
bash scripts/setup.sh --rapid-v23 --qwen-edit-2511 both --qwen21-int8-encoder
sudo docker compose up -d
```

Ohne `--qwen21-int8-encoder` lädt das Setup weiterhin die bisherigen Standardmodelle. Eigene LoRAs müssen separat kopiert werden; beim Serverumzug bestehende `.env` und Anwendungsdaten für gespeicherte Zuordnungen übernehmen.

## Vergleich auf der GPU

Vier Bilder mit demselben Prompt und Seed: beide Encoder, jeweils bisherige Workflow-Einstellungen und ER-SDE/Beta:

```bash
bash studio-compose.sh exec -T web python - --compare-defaults \
  < scripts/test-qwen21-options.py
bash studio-compose.sh cp web:/data/qwen21-options-tests ~/qwen21-options-tests
```

Optional `--all-expansions` für Off, Standard und Heretic ergänzen. `--encoder bf16` testet ohne zusätzlichen Encoder. `--reference /data/test/reference.png` kann bis zu zehnmal angegeben werden; die Dateien müssen im Web-Container vorhanden sein. `--quality high` testet 2K. Bilder und JSON mit Einstellungen, Zeit und erweitertem Prompt werden unter `/data/qwen21-options-tests` gespeichert. Aktive Qwen-LoRAs gelten auch für den Vergleich; ihre Einstellungen währenddessen konstant lassen.

## Quellen und Grenzen

- Encoder: [Stick9190/qwen3vl_8b_int8_convrot](https://huggingface.co/Stick9190/qwen3vl_8b_int8_convrot), Revision `161a0674c90d4aa82cd19d9b02630263314451e5`.
- SHA-256: `5deb0b5742b7ac2b1d2a9c6c48433a98b9444954b6114b616812ebd5d977296e`.
- [ComfyUI Sampler/Scheduler](https://github.com/Comfy-Org/ComfyUI/blob/f1072eb0350638a3390ddb6afbcaa8c6b237c6fd/comfy/samplers.py) und [ConvRot-Unterstützung](https://github.com/Comfy-Org/ComfyUI/blob/f1072eb0350638a3390ddb6afbcaa8c6b237c6fd/comfy/ops.py) sind bereits in der verwendeten Revision enthalten.

INT8 ist eine verlustbehaftete Quantisierung mit einigen BF16-Schichten. Die Dateigröße entspricht nicht dem gesamten VRAM-Bedarf. Weder ein Geschwindigkeitsgewinn noch bessere Bildqualität durch ER-SDE/Beta sind zugesichert. Die automatisierten Prüfungen verwenden einen simulierten ComfyUI-Dienst; echte CUDA-Ausführung und Bildqualität müssen auf dem Server geprüft werden.
