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

Kopiere `image-studio-setup.zip` in dein Home-Verzeichnis auf dem Server. Klone außerdem das aktuelle Repository, falls es dort noch nicht vorhanden ist:

```bash
git clone https://github.com/Ben-Joel-Schoenbein/image-generation-platform.git ~/Imagegen-server-new
python3 -m zipfile -e ~/image-studio-setup.zip ~/
sudo -v
bash ~/image-studio-setup/scripts/setup.sh --project ~/Imagegen-server-new
cd ~/Imagegen-server-new
sudo docker compose up -d
```

Während des Setups wirst du nach Generator-Domain, Archiv-Domain und den Zugangsdaten gefragt. Bei den beiden Passwörtern erzeugt Enter jeweils ein zufälliges Passwort. Ein Discord-Bot-Token ist optional; mit Enter bleibt der Bot deaktiviert.

Neu eingerichtete Generator- und Archiv-Zugangsdaten stehen anschließend im Projekt unter `setup-credentials.txt`. Diese Datei und `.env` sind nur für den ausführenden Benutzer lesbar. Sie werden zusammen mit Modellen und lokalen Systemprompts in `.gitignore` aufgenommen.

Das Setup setzt `COMPOSE_FILE` in `.env` auf die Compose-Dateien einschließlich `compose.heretic.yaml`. Discord wird über `COMPOSE_PROFILES` aktiviert, sobald ein Token hinterlegt ist. Dadurch funktioniert der normale Compose-Start. Für diese Variablen dürfen keine abweichenden Werte in der aufrufenden Shell exportiert sein; das Script erkennt solche Überschreibungen und bricht ab.

## Setup und überarbeitete README ins Repository übernehmen

Damit zukünftige Installationen direkt aus dem Repository funktionieren, kopiere nach dem Entpacken die drei Setup-Dateien in dessen `scripts`-Ordner:

```bash
mkdir -p ~/Imagegen-server-new/scripts
cp ~/image-studio-setup/scripts/setup.sh ~/Imagegen-server-new/scripts/
cp ~/image-studio-setup/scripts/setup.py ~/Imagegen-server-new/scripts/
cp ~/image-studio-setup/scripts/model-manifest.json ~/Imagegen-server-new/scripts/
cp ~/image-studio-setup/repository/README.md ~/Imagegen-server-new/README.md
```

Die Datei `repository/README.md` ist die überarbeitete Haupt-README des Projekts. Sie ersetzt die alte Startanleitung und die veralteten Modellangaben. Prüfe den Diff, wenn du in deiner eigenen README weitere Änderungen vorgenommen hast.

Nimm die drei Setup-Dateien und die überarbeitete `README.md` in deinen nächsten Commit auf. Anschließend lautet der Ablauf auf einem vorbereiteten neuen Server:

```bash
git clone https://github.com/Ben-Joel-Schoenbein/image-generation-platform.git
cd image-generation-platform
bash scripts/setup.sh
sudo docker compose up -d
```

Dieses Paket wurde bereitgestellt; die Dateien wurden nicht automatisch in dein GitHub-Repository geschrieben.

## Enthaltene Modelle und Konfiguration

| Verwendung | Dateien |
| --- | --- |
| Aktuelle Qwen-Image-2.1-Workflows | BF16-Diffusionsmodell, Qwen3-VL-8B-Encoder, BF16-VAE und beide regulären PE-Encoder in `int8_convrot` |
| Rapid AIO | `Qwen-Rapid-AIO-NSFW-v19.safetensors` |
| Heretic für Textprompts | `pe_t2i_heretic-Q4_K_M.gguf`, Systemprompt und Lizenz |
| Heretic für Bildbearbeitung | `pe_i2i_heretic-Q4_K_M.gguf`, BF16-Multimodal-Projektor, Systemprompt und Lizenz |

Es sind 13 Dateien mit zusammen 92.518.531.074 Bytes. Die Quellen stehen mit festen Hugging-Face-Revisionen und Prüfsummen in `scripts/model-manifest.json`. Die vollständigen Dateinamen werden vor dem Download angezeigt.

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

## Bestehende Installation auf einen anderen Server umziehen

Für eine neue, leere Installation reicht nach der Vorbereitung der Compose-Start. Für einen Umzug mit bestehenden Benutzern, Einstellungen und Bildern musst du zusätzlich die **ursprüngliche `.env`**, `data/`, `media-files/` und einen konsistenten Backup/Restore der PostgreSQL-Datenbank übernehmen. ComfyUI-Ausgaben liegen zusätzlich unter `comfy-output/`; vorhandene Modelle kannst du ebenfalls kopieren, um Downloads zu sparen.

Benutzer und Anwendungseinstellungen liegen teilweise in Datenbanken. Sie werden nicht aus GitHub heruntergeladen. Wenn das Setup vorhandene Generator-Daten oder ein vorhandenes Archiv-Datenbank-Volume erkennt, aber ein neues Datenbank-/Admin-Passwort erzeugen müsste, bricht es ab und fordert die ursprüngliche `.env`. Bestehende Daten werden vom Setup nicht gelöscht.

## Geprüft

- 19 automatisierte Prüfungen für Konfiguration, Erhaltung bestehender Einstellungen, Datenbankschutz, Dateirechte, Prüfsummen, fortsetzbare Downloads und Systemprompts.
- Bash-Syntax und Offline-Prüfung gegen den aktuell geprüften Repository-Stand.
- Die vier kleinen Heretic-Systemprompt-/Lizenzdateien wurden tatsächlich von den gepinnten Hugging-Face-Revisionen heruntergeladen und anhand ihrer Git-Blob-Hashes geprüft.

Die kompletten 93 GB und ein vollständiger Docker/GPU-Start konnten in der Prüfungsumgebung nicht ausgeführt werden. Der reale Container-Build und GPU-Test erfolgen beim Setup auf deinem Server.

Tests aus diesem entpackten Paket ausführen:

```bash
python3 tests/test_setup.py
```
