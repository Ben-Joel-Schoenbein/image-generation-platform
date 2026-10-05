# Qwen-Image-Edit-2509 für Friends Image Studio

**Aktueller Stand:** Der Workflow verwendet jetzt den mitgelieferten
`StudioQwenImageEditReferences`-Knoten für bis zu zehn Einzelbilder.
Die aktuellen Installationsbefehle und Grenzen stehen in
[docs/update-multi-reference-de.md](../docs/update-multi-reference-de.md).
Ein ComfyUI-Neubau ist für diesen Knoten erforderlich.


Dieses Zusatzpaket enthält einen API-Workflow für ein bis zehn Referenzbilder und eine
Textanweisung, einen Downloader für vier Modelldateien sowie einen Prüfer für
die laufende ComfyUI-Instanz. Es enthält keine Modellgewichte.

Der Workflow verwendet Qwen-Image-Edit-2509 FP8 mit der dazugehörigen
LightX2V-4-Schritt-LoRA. Die Ausgabe behält das Seitenverhältnis des
Referenzbilds und wird ungefähr auf eine Million Pixel skaliert.

## Hardware zuerst prüfen

Auf der OpenStack-Instanz:

```bash
nvidia-smi
free -h
df -h .
```

Die vorhandene Compose-Konfiguration erwartet eine NVIDIA-GPU und das NVIDIA
Container Toolkit. Falls `nvidia-smi` fehlt oder keine GPU erkennt, zuerst
GPU-Zuweisung und Treiber prüfen. Eine Instanz mit viel Arbeitsspeicher hat
nicht automatisch eine GPU.

Die vier Downloads sind zusammen ungefähr 31 GB groß. Plane mindestens 40 GB
freien Plattenplatz zusätzlich zu Docker-Images, anderen Modellen und Bildern.
Plattenplatz ist kein GPU-Speicher. Ob der Workflow auf deiner GPU läuft, hängt
von VRAM, RAM, Treiber, PyTorch und CPU-Offloading ab; aus den Dateigrößen folgt
kein garantierter VRAM-Mindestwert.

## Installation per Git

Entpacke `qwen-edit-setup.zip` auf deinem PC in den lokalen Git-Projektordner.
Danach muss dort der Ordner `qwen-edit-setup/` neben `compose.yaml` liegen.

Kopiere auf dem PC `qwen-edit-setup/workflow.api.json` nach
`workflows/img2img.api.json`. In Windows PowerShell:

```powershell
Copy-Item .\qwen-edit-setup\workflow.api.json .\workflows\img2img.api.json
git add qwen-edit-setup workflows/img2img.api.json
git commit -m "Configure Qwen Image Edit 2509"
git push
```

Auf der Instanz das Repository aktualisieren:

```bash
cd ~/image-studio
GIT_SSH_COMMAND='ssh -i ~/.ssh/image-studio-deploy -o IdentitiesOnly=yes' git pull --ff-only
sudo apt update
sudo apt install -y curl
bash qwen-edit-setup/download-models.sh
```

Der Downloader startet ohne sudo, legt die passenden `models/`-Unterordner an,
setzt unterbrochene Downloads fort und prüft SHA-256. Bereits vorhandene Dateien
werden geprüft und bei passender Prüfsumme übersprungen. Eine Datei mit falscher
Prüfsumme wird nicht automatisch überschrieben. Ein fehlgeschlagener Download
bleibt mit `.part`-Endung liegen und wird nicht als Modell geladen.

Nun ComfyUI mit einem frischen Build aktualisieren und die Dienste starten:

```bash
sudo docker compose build --no-cache comfyui
sudo docker compose up -d comfyui web
sudo docker compose logs --tail=100 comfyui
```

Der Build verwendet das vorhandene `PYTORCH_BASE` aus deiner `.env` bzw. den
Compose-Standard. Wenn Start-Logs eine inkompatible GPU, einen CUDA-Treiber oder
fehlende PyTorch-Funktionen melden, muss dieses Image passend zur Hardware
angepasst werden.

Die Compose-Konfiguration des ursprünglichen Projekts mountet `models/` in
ComfyUI und lädt `workflows/img2img.api.json` im Webdienst. Dafür ist keine neue
Domain und kein weiterer öffentlicher Port nötig. Der Textmodus verwendet
weiter seinen SDXL-Workflow; dessen eigene Modelldatei lädt
`bash scripts/download-sdxl.sh` herunter.

## Installation ohne lokales Git-Checkout

Alternativ kannst du das ZIP per SCP oder WinSCP in den Projektordner auf dem
Server kopieren und dort entpacken. Ersetze die Platzhalter und verwende deinen
üblichen SSH-Schlüssel. In PowerShell auf deinem PC, im Downloadordner:

```powershell
scp .\qwen-edit-setup.zip ubuntu@DEINE_SERVER_IP:~/image-studio/
```

Danach auf der Instanz:

```bash
cd ~/image-studio
sudo apt install -y unzip curl
unzip qwen-edit-setup.zip
cp -n workflows/img2img.api.json ~/img2img-before-qwen.api.json
cp qwen-edit-setup/workflow.api.json workflows/img2img.api.json
bash qwen-edit-setup/download-models.sh
sudo docker compose build --no-cache comfyui
sudo docker compose up -d comfyui web
```

Diese Variante ändert eine von Git verwaltete Workflow-Datei auf dem Server.
Prüfe vor späteren `git pull`-Aufrufen `git status` und übernimm die Änderung
in dein Repository, wenn Qwen dauerhaft genutzt werden soll.

## Vor der ersten Generierung prüfen

Wenn ComfyUI gestartet ist, im Projektordner auf der Instanz ausführen:

```bash
sudo docker compose exec -T web python - /workflows/img2img.api.json < qwen-edit-setup/check-workflow.py
```

Der Prüfer fragt die interne `/object_info`-Schnittstelle ab. Er prüft, ob die
Knoten, Verbindungen, erforderlichen Felder und Modellnamen vorhanden sind.
Er startet keine Generierung und belegt keinen Speicher durch Modellladen.

Öffne danach deine Generator-Webseite, wähle **Edit using a reference image**,
lade ein PNG/JPEG/WebP hoch und gib beispielsweise diese Anweisung ein:

> Keep the character's face, hairstyle and armor. Place the character in a rainy
> futuristic city at night, in a detailed comic style.

Beim ersten Durchlauf müssen die Modelle geladen werden. Falls eine Generierung
fehlschlägt, im Projektordner die Logs ansehen:

```bash
sudo docker compose logs --tail=150 web comfyui
```

Die bestehende App wartet maximal sieben Minuten auf eine Generierung. Bei
langsamem CPU-Offloading kann dieses Limit erreicht werden. Ein Timeout der
Website beendet einen bereits laufenden ComfyUI-Auftrag nicht automatisch.

## Quellen und Grenzen

- ComfyUI-Modell-Dateien und native Qwen-Knoten:
  https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI
  https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI
  https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_qwen.py
- Modellpfade aus dem offiziellen ComfyUI-Template:
  https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_edit_2509.json
- Lightning-LoRA und 4-Schritt-Sampler-Einstellungen:
  https://huggingface.co/lightx2v/Qwen-Image-Lightning
  https://github.com/ModelTC/LightX2V-Qwen-Image-Lightning/blob/main/workflows/qwen-image-edit-2509-4steps.json

Der Workflow ist eine eigenständig erstellte Integration der nativen Knoten für
den Platzhaltermechanismus des Projekts (`{{PROMPT}}`, `{{IMAGE}}`, `{{SEED}}`).
JSON-Struktur, Verbindungen, Platzhalterersetzung sowie Shell- und Python-Syntax
wurden geprüft. Ein Docker/GPU-Lauf wurde in der Erstellungsumgebung nicht
ausgeführt. Die Modell-Prüfsummen stammen von den verlinkten Hugging-Face-Seiten
(Stand 4. Oktober 2026).

Das aktuelle Webformular und dieser Workflow verarbeiten ein Referenzbild pro
Anfrage. Für mehrere Bilder müsste auch der Upload im Webdienst und im
Discord-Bot erweitert werden. Die vorhandenen Prompt-Sperrregeln werden vom
Webdienst weiterhin angewendet; der Modellwechsel ergänzt keinen Bildfilter.
