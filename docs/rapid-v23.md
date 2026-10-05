# Qwen Rapid AIO v23 NSFW integrieren und vergleichen

Die Anwendung bietet Qwen Image 2.1, Rapid AIO v19 NSFW und Rapid AIO v23 NSFW an. Nur die NSFW-Variante von v23 wird installiert. Die Auswahl ist bewusst im Code hinterlegt: Eine zusätzliche Datei im Modellordner allein erweitert Website und Discord nicht. ComfyUI erkennt Checkpoints, die Anwendung benötigt zusätzlich einen kompatiblen Workflow und eine Modellzuordnung.

## Bestehenden Server aktualisieren

Falls du das Integrationspaket verwendest, kopiere `qwen-rapid-v23.zip` nach `~/` auf dem Server und führe aus:

```bash
python3 -m zipfile -e ~/qwen-rapid-v23.zip ~/
python3 ~/qwen-rapid-v23/apply.py --project ~/image-studio --check
python3 ~/qwen-rapid-v23/apply.py --project ~/image-studio
cd ~/image-studio
python3 scripts/download-rapid-v23.py
bash studio-compose.sh up -d --build web discord
bash studio-compose.sh logs --tail=80 web discord
```

Der Installer prüft alle betroffenen Quelldateien vor der ersten Änderung. Bei abweichenden lokalen Änderungen hält er an. Eine Sicherung der geänderten Dateien wird neben dem Repository in `image-studio-rapid-v23-backups/` angelegt. Das Paket enthält die Änderungen zum Review auch als `integration.patch` und unter `files/`. Die Integrationsdateien können nach Prüfung mit deinem nächsten Commit gepusht werden.

Der gezielte Downloader installiert nur den v23-Checkpoint nach `models/checkpoints/Qwen-Rapid-AIO-NSFW-v23.safetensors`. Der Download umfasst 28.431.840.023 Bytes. Er setzt `.part`-Downloads fort und prüft Größe sowie SHA-256 vor der endgültigen Umbenennung. Die Speicherprüfung berücksichtigt zusätzlich 20 GiB Reserve; ohne vorhandenen Teildownload sind etwa 49,9 GB frei erforderlich. Der vorhandene v19-Checkpoint bleibt erhalten.

Das ComfyUI-Modellverzeichnis ist bereits eingebunden. Der bestehende AIO-Loader und der Encoder für vier Referenzbilder werden weiterverwendet. Für v23 sind keine zusätzlichen CLIP-, VAE- oder Heretic-Dateien erforderlich. Die laufende ComfyUI-Version muss die bestehende v19-Integration unterstützen.

## Website und Discord

In der Website im Modellfeld **Qwen Rapid AIO v23 — NSFW** auswählen. In Discord bei `/imagine` oder `/edit` dieselbe Option unter `model` wählen. Beide Wege erlauben 4, 6 oder 8 Schritte; beginne mit **4 Schritten**, **1K** und **Prompt-Erweiterung Off**. Intern werden CFG 1, `euler_ancestral` und `beta` verwendet.

Rapid AIO erlaubt maximal **vier Referenzbilder**. Das Ausgabeformat folgt dem Seitenverhältnis von Bild 1. Qwen Image 2.1 unterstützt weiterhin bis zu zehn Referenzbilder. Standard und Heretic sind für v19 und v23 ausdrücklich auswählbar. Bei Auto bleibt die Erweiterung für Rapid deaktiviert, sofern `enhance_prompt` nicht ausdrücklich aktiviert wird.

Der Bot synchronisiert seine Optionen beim Start. Falls Discord noch die bisherige Auswahl anzeigt, **Ctrl+R** drücken oder die App vollständig neu starten und danach einen neuen Slash-Command eingeben.

## Automatischer Vergleich auf deiner GPU

Der Test nutzt die normale interne Hintergrundjob-API und die vorhandene Generierungswarteschlange. Er sendet keine Discord-Nachrichten. Während des Vergleichs sollten keine weiteren Generierungsaufträge gestartet werden. Bei bereits aktiven Aufträgen hält das Script vor dem Test an.

```bash
cd ~/image-studio
bash studio-compose.sh exec -T web python - --compare-v19 < scripts/test-rapid-v23.py
bash studio-compose.sh cp web:/data/rapid-v23-tests ~/rapid-v23-tests
```

Es entstehen ein v19- und ein v23-Bild mit identischem Prompt, Seed 12345, vier Schritten, 1K und deaktivierter Erweiterung. Bilder, Laufzeiten und Einstellungen stehen in den Ergebnisdateien. Ohne `--compare-v19` wird nur v23 getestet. Die Testbilder liegen getrennt von der persönlichen Galerie unter `/data/rapid-v23-tests` im Web-Container.

Für eigene Bildbearbeitungen gib den Pfad eines vorhandenen Referenzbildes **im Web-Container** an; bis zu viermal möglich:

```bash
bash studio-compose.sh exec -T web python - \
  --compare-v19 --reference /data/images/USER_ID/IMAGE_ID.png \
  --prompt "Replace the background with a beach at sunset; preserve the person in Image 1" \
  < scripts/test-rapid-v23.py
```

`USER_ID` und `IMAGE_ID` durch ein vorhandenes Bild ersetzen. Mit `--prompt-expansion heretic` oder `--prompt-expansion standard` lässt sich anschließend die Erweiterung prüfen. Mit `--quality high` und `--steps 6` / `8` lassen sich weitere Varianten testen. Beim Vergleich jeweils dieselben Referenzen, denselben Prompt und dieselben Einstellungen verwenden; identische Seeds erleichtern die Wiederholbarkeit, garantieren aber zwischen unterschiedlichen Modellversionen keine identischen Bildinhalte.

Achte auf die Umsetzung einzelner Promptdetails, Gesicht und Hände, Erhaltung von Personen/Objekten bei Bearbeitungen und Laufzeit. Laut [Modellautor](https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO) kann v23 Prompts besser befolgen, während v19 für Bearbeitungen konsistenter sein kann. Behalte die Version, die mit deinen Eingaben besser funktioniert.

## Neuinstallation und Prüfungen

```bash
bash scripts/setup.sh --rapid-v23
sudo docker compose up -d
```

Mit v23 installiert das Setup etwa 121 GB Modelle; ohne Flag weiterhin etwa 93 GB. Für eine reine Offline-Prüfung:

```bash
bash scripts/setup.sh --check --rapid-v23
python3 scripts/download-rapid-v23.py --check
```

Die automatisierten Integrationstests brauchen keine GPU oder Modelldownloads. In einer separaten Python-Umgebung die Web- und Discord-Abhängigkeiten sowie pytest installieren; für den Browser-Steuerungstest muss Node.js verfügbar sein:

```bash
python3 -m venv /tmp/image-studio-v23-tests
/tmp/image-studio-v23-tests/bin/pip install -r web/requirements.txt -r discord/requirements.txt pytest
/tmp/image-studio-v23-tests/bin/python -m pytest -q tests/test_rapid_v23.py tests/test_setup.py
```

Die Integration wurde mit simulierten ComfyUI-Antworten geprüft. Der vollständige Checkpoint-Download, das Laden auf der A40 und die visuelle Qualität müssen mit den obigen Server-Tests geprüft werden.
