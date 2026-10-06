# Image Studio: Setup für einen neuen GPU-Server

Dieses Paket bereitet den aktuellen Stand von `Ben-Joel-Schoenbein/image-generation-platform` vor. Es lädt die von den vorhandenen Workflows verwendeten Modelle, richtet die private `.env` ein und baut die Container. Danach startest du die Anwendung im Repository mit:

```bash
sudo docker compose up -d
```

Das Setup startet oder stoppt keine laufende Anwendung.

## Voraussetzungen

- Linux-Server mit passender NVIDIA-GPU; die bisherige A40 mit 48 GB ist die vorgesehene Umgebung.
- Docker Engine mit Compose-Plugin, NVIDIA-Treiber und NVIDIA Container Toolkit müssen bereits eingerichtet sein.
- Python 3.9 oder neuer, `curl` und Internetzugriff auf Hugging Face sowie die verwendeten Container- und Paketregistries.
- Zwei verschiedene Domains für Generator und Archiv, deren DNS auf den Server zeigt; Ports 80 und 443 müssen erreichbar sein.
- Etwa **93 GB für alle Modelldateien**, zusätzlich Platz für Docker-Images, Builds und erzeugte Bilder. Das Script reserviert beim Download weitere 20 GiB; bei einem frischen Server entsprechend mehr Platz für zuvor heruntergeladene Container-Images einplanen.

Das Script installiert keine Betriebssystempakete, Treiber oder DNS-Einträge. `docker info`, `docker compose version` und `nvidia-smi` müssen funktionieren. Falls Docker nur mit `sudo` funktioniert, vor dem Setup `sudo -v` ausführen. Das Setup prüft anschließend den GPU-Zugriff mit dem PyTorch/CUDA-Image aus deiner Compose-Konfiguration.

## Einmal auf einem neuen Server ausführen

Klone das aktuelle Repository und starte das darin gespeicherte Setup:

```bash
git clone https://github.com/Ben-Joel-Schoenbein/image-generation-platform.git ~/image-studio
cd ~/image-studio
sudo -v
bash scripts/setup.sh
sudo docker compose up -d
```

Für v23 NSFW `--rapid-v23` ergänzen. Die zusätzlichen Edit-2511-Modelle erhältst du mit `--qwen-edit-2511 fp8`, `bf16` oder `both`. Beide Varianten brauchen zusammen 71 GB zusätzliche Modelldateien. [Installation und Vergleich](docs/qwen-edit-2511.md).

Während des Setups wirst du nach Generator-Domain, Archiv-Domain und den Zugangsdaten gefragt. Bei den beiden Passwörtern erzeugt Enter jeweils ein zufälliges Passwort. Ein Discord-Bot-Token ist optional; mit Enter bleibt der Bot deaktiviert.

Neu eingerichtete Generator- und Archiv-Zugangsdaten stehen anschließend im Projekt unter `setup-credentials.txt`. Diese Datei und `.env` sind nur für den ausführenden Benutzer lesbar. Sie werden zusammen mit Modellen und lokalen Systemprompts in `.gitignore` aufgenommen.

Das Setup setzt `COMPOSE_FILE` in `.env` auf die Compose-Dateien einschließlich `compose.heretic.yaml`. Discord wird über `COMPOSE_PROFILES` aktiviert, sobald ein Token hinterlegt ist. Dadurch funktioniert der normale Compose-Start. Für diese Variablen dürfen keine abweichenden Werte in der aufrufenden Shell exportiert sein; das Script erkennt solche Überschreibungen und bricht ab.

## Setup im Repository

Die Setup-Dateien liegen im Repository unter `scripts/`. Nach Übernahme der v23-Integration sind dort auch `download-rapid-v23.py` und `test-rapid-v23.py` verfügbar. Modelle, private Zugangsdaten und bestehende Bilder werden lokal auf dem Server gespeichert.

## Rapid AIO v23 NSFW auf dem bestehenden Server ergänzen

Nachdem die v23-Integrationsdateien im Repository liegen, genügt im Projektordner:

```bash
python3 scripts/download-rapid-v23.py
bash studio-compose.sh up -d --build web discord
```

Es wird nur `Qwen-Rapid-AIO-NSFW-v23.safetensors` zusätzlich geladen: etwa 28,4 GB mit fortsetzbarem Download und SHA-256-Prüfung. Die SFW-Variante ist nicht enthalten. Das Script reserviert außerdem 20 GiB freien Speicher. Der bestehende v19-Checkpoint bleibt verfügbar.

Für einen neuen Server kann v23 direkt beim Setup mitgeladen werden:

```bash
bash scripts/setup.sh --rapid-v23
sudo docker compose up -d
```

Damit sind es insgesamt etwa 121 GB Modelldateien. Ohne `--rapid-v23` lädt das Setup die bisherigen 13 Dateien mit etwa 93 GB.

In Website und Discord unter `model` **Qwen Rapid AIO v23 — NSFW** wählen. Mit 4 Schritten, 1K und Prompt-Erweiterung Off beginnen. Standard und Heretic lassen sich weiterhin ausdrücklich auswählen. Rapid AIO unterstützt maximal vier Referenzbilder; Qwen Image 2.1 weiterhin zehn. Die Discord-Auswahl nach dem Neustart des Bots mit Ctrl+R aktualisieren und einen neuen Slash-Command beginnen.

Ein automatischer Vergleich erzeugt je ein Bild mit v19 und v23 bei denselben Einstellungen:

```bash
bash studio-compose.sh exec -T web python - --compare-v19 < scripts/test-rapid-v23.py
bash studio-compose.sh cp web:/data/rapid-v23-tests ~/rapid-v23-tests
```

Die Bilder und JSON-Dateien mit Laufzeit und Einstellungen liegen danach unter `~/rapid-v23-tests`. Der vollständige Testablauf mit eigenen Referenzen und Prompt-Erweiterung steht in [docs/rapid-v23.md](docs/rapid-v23.md).

## Enthaltene Modelle und Konfiguration

| Verwendung | Dateien |
| --- | --- |
| Aktuelle Qwen-Image-2.1-Workflows | BF16-Diffusionsmodell, Qwen3-VL-8B-Encoder, BF16-VAE und beide regulären PE-Encoder in `int8_convrot` |
| Rapid AIO v19 NSFW | `Qwen-Rapid-AIO-NSFW-v19.safetensors` |
| Optional Edit 2511 FP8 Mixed/BF16 | Diffusionsmodell(e), gemeinsamer Qwen-2.5-VL-FP8-Encoder und Qwen-Image-VAE |
| Optional Rapid AIO v23 NSFW | `Qwen-Rapid-AIO-NSFW-v23.safetensors` |
| Heretic für Textprompts | `pe_t2i_heretic-Q4_K_M.gguf`, Systemprompt und Lizenz |
| Heretic für Bildbearbeitung | `pe_i2i_heretic-Q4_K_M.gguf`, BF16-Multimodal-Projektor, Systemprompt und Lizenz |

Ohne v23 sind es 13 Dateien mit zusammen 92.518.531.074 Bytes; mit `--rapid-v23` zusätzlich 28.431.840.023 Bytes. Die Quellen stehen mit festen Hugging-Face-Revisionen und Prüfsummen in `scripts/model-manifest.json`. Die vollständigen Dateinamen werden vor dem Download angezeigt.

Die Systemprompts und Lizenzen werden zusätzlich nach `web/heretic_prompts/` kopiert, damit der vorhandene Web-Dockerfile sie beim Build übernehmen kann. `prompt-enhancer/models.ini`, Workflows und Anwendungscode werden aus dem vorhandenen Checkout verwendet. Das Setup prüft, dass ihre Modellpfade zum Manifest passen.

Bestehende `.env`-Werte bleiben erhalten. Fehlende oder erkennbare Beispielwerte werden ausgefüllt; zufällige Secrets werden lokal erzeugt. Vor Änderungen an einer vorhandenen `.env` wird eine private Sicherung `.env.setup-backup-…` erstellt. Ein vorhandener Compose-Override wird berücksichtigt.

## Wiederholen und Downloads fortsetzen

Führe nach einem Verbindungsabbruch denselben Setup-Befehl erneut aus. Downloads werden zunächst als `.part` gespeichert und fortgesetzt. Erst nach erfolgreicher Größen- und Prüfsummenprüfung erhält die Datei ihren endgültigen Namen. Bereits vollständige Dateien werden geprüft und erneut verwendet.

Weicht eine vorhandene Modelldatei oder ein lokaler Heretic-Systemprompt ab, überschreibt das Setup sie nicht. Verschiebe die betreffende Datei bewusst an einen anderen Ort, wenn du stattdessen die Version aus diesem Paket verwenden möchtest. Eine bereits vollständig heruntergeladene `.part` mit falscher Prüfsumme muss ebenfalls zuerst verschoben werden.

Bei später geänderten Workflows mit anderen Modellen muss das Manifest aktualisiert werden. Das Setup bricht bei unbekannten Modellreferenzen ab.

## Optionen

Nur Checkout und Modellbedarf prüfen, ohne Downloads oder Änderungen:

```bash
bash scripts/setup.sh --check
```

Diese Offline-Prüfung validiert keine GPU, keine Docker-Installation und keine vollständige Server-Konfiguration.

Ohne Eingaben einrichten; neue Passwörter werden erzeugt und ein noch nicht konfigurierter Discord-Bot bleibt deaktiviert:

```bash
bash scripts/setup.sh --non-interactive \
  --domain bilder.deine-domain.de \
  --library-domain archiv.deine-domain.de
```

Nur vorbereiten, ohne Container zu bauen:

```bash
bash scripts/setup.sh --no-build
sudo docker compose up -d --build
```

## LoRAs automatisch verwenden

Lege passende LoRA-Dateien unter `models/loras/` ab; Unterordner werden ebenfalls erkannt. Das Setup erstellt den Ordner. Neue Dateien werden beim nächsten Rapid-AIO-Auftrag automatisch mit Stärke **0,6** aktiviert. Die Einstellungen gelten gemeinsam für v19 NSFW und v23 NSFW sowie für Website und Discord.

Als Administrator findest du unter **LoRAs** (`/admin/loras`) die Standardstärke für neue Dateien, eine Stärke pro Datei und Schalter zum einzelnen oder vollständigen Deaktivieren. Zulässig sind -2 bis 2; Stärke 0 deaktiviert eine Datei. Änderungen nach dem Speichern gelten für nachfolgende Aufträge. `/loras` zeigt in Discord die Dateien und ihre Stärken. Qwen Image 2.1 verwendet weiterhin seinen bisherigen Workflow.

Die Einstellungen werden in der SQLite-Datenbank unter `data/` gespeichert. Beim Serverumzug `data/` und `models/loras/` übernehmen. Das Setup erstellt den Ordner, lädt aber keine LoRA-Dateien herunter. Die automatische Erkennung prüft nicht, ob eine Datei zum Modell passt. Details zur Installation und zur Übernahme bisher fest eingetragener LoRAs stehen in [docs/automatic-loras.md](docs/automatic-loras.md).

## Bestehende Installation auf einen anderen Server umziehen

Für eine neue, leere Installation reicht nach der Vorbereitung der Compose-Start. Für einen Umzug mit bestehenden Benutzern, Einstellungen und Bildern musst du zusätzlich die **ursprüngliche `.env`**, `data/`, `media-files/` und einen konsistenten Backup/Restore der PostgreSQL-Datenbank übernehmen. ComfyUI-Ausgaben liegen zusätzlich unter `comfy-output/`; vorhandene Modelle kannst du ebenfalls kopieren, um Downloads zu sparen.

Benutzer und Anwendungseinstellungen liegen teilweise in Datenbanken. Sie werden nicht aus GitHub heruntergeladen. Wenn das Setup vorhandene Generator-Daten oder ein vorhandenes Archiv-Datenbank-Volume erkennt, aber ein neues Datenbank-/Admin-Passwort erzeugen müsste, bricht es ab und fordert die ursprüngliche `.env`. Bestehende Daten werden vom Setup nicht gelöscht.

## Geprüft

Die v23-Integration ergänzt automatisierte Prüfungen für beide NSFW-Modellversionen, Website/API, echte Discord-Registrierung, Prompt-Erweiterung, Referenzlimits und den optionalen Download. Der GPU-Vergleich läuft mit `scripts/test-rapid-v23.py` auf deinem Server.

- 19 automatisierte Prüfungen für Konfiguration, Erhaltung bestehender Einstellungen, Datenbankschutz, Dateirechte, Prüfsummen, fortsetzbare Downloads und Systemprompts.
- Bash-Syntax und Offline-Prüfung gegen den aktuell geprüften Repository-Stand.
- Die vier kleinen Heretic-Systemprompt-/Lizenzdateien wurden tatsächlich von den gepinnten Hugging-Face-Revisionen heruntergeladen und anhand ihrer Git-Blob-Hashes geprüft.

Die kompletten 93 GB und ein vollständiger Docker/GPU-Start konnten in der Prüfungsumgebung nicht ausgeführt werden. Der reale Container-Build und GPU-Test erfolgen beim Setup auf deinem Server.

Tests aus diesem entpackten Paket ausführen:

```bash
python3 tests/test_setup.py
```

## LoRAs für Qwen 2.1 und Edit 2511

Unter **Admin → LoRAs → Apply to models** lassen sich die erlaubten Einzelmodelle je Datei anhaken und die Stärke einstellen. Zur Auswahl stehen Qwen Image 2.1, AIO v19, AIO v23, Edit 2511 FP8 Mixed und BF16. Eine leere Auswahl deaktiviert die Anwendung dieser Datei. Dateien in `models/loras/qwen21/` werden standardmäßig Qwen 2.1 zugeordnet, andere Dateien Edit 2511/Rapid AIO. Bestehende Stärken und Familienzuordnungen bleiben erhalten, bis du die Häkchen änderst. Website und Discord verwenden ausschließlich die dem gewählten Modell zugeordneten LoRAs. Siehe [LoRA-Anleitung](docs/automatic-loras.md). Beschleunigungs-LoRAs können einen eigenen Sampling-Workflow benötigen.
