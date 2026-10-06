# Automatische LoRAs für Qwen

Die Website und der Discord-Bot verwenden dieselbe LoRA-Konfiguration. Sie gilt für Qwen Image 2.1, die Rapid-AIO-Checkpoints v19/v23 NSFW und Edit 2511 FP8 Mixed/BF16. Jede LoRA wird ausschließlich auf die von dir ausgewählten Modelle angewendet, auch mit Standard- oder Heretic-Prompt-Erweiterung.

## Dateien und Stärken

1. Lege zum Modell passende LoRAs in `models/loras/` ab. Dateien unter `models/loras/qwen21/` werden standardmäßig Qwen 2.1 zugeordnet, alle anderen Edit 2511/Rapid AIO. Auch `models/loras/stile/mein-stil.safetensors` wird erkannt.
2. Melde dich auf der Website als Administrator an und öffne **LoRAs** oder direkt `/admin/loras`.
3. Setze je Datei unter **Apply to models** die Häkchen für die gewünschten Modelle: Qwen Image 2.1, Rapid AIO v19, Rapid AIO v23, Edit 2511 FP8 Mixed und Edit 2511 BF16. Mehrere Häkchen sind möglich. Stelle anschließend die Standardstärke und eigene Stärken ein. Speichere die Einstellungen.
4. Generiere mit dem zugeordneten Modell. Mehrere aktive LoRAs werden gemeinsam angewendet, in alphabetischer Reihenfolge ihrer relativen Dateinamen. In Discord zeigt `/loras` die Dateien, Modellzuordnungen und Stärken; `/imagine` und `/edit` übernehmen die Einstellungen automatisch.

Alle neu erkannten Dateien sind automatisch aktiviert; die anfängliche Standardstärke ist **0,6**. Der Wertebereich beträgt **-2 bis 2**. Stärke **0**, ein deaktiviertes **Enabled**-Kontrollkästchen oder eine leere Modellauswahl überspringt die betreffende Datei. Der globale Schalter deaktiviert die automatische Anwendung vollständig. Gespeicherte Einstellungen für vorübergehend fehlende Dateien bleiben erhalten.

ComfyUI liefert die Dateiliste bei jedem Bildauftrag und jedem Aufruf der Einstellungsseite. Eine neue Datei benötigt deshalb keinen Container-Neubau. Auf der geöffneten Einstellungsseite aktualisiert **Refresh file list** die Liste. Aktuell laufende Generierungen behalten ihre bereits geladenen Einstellungen. Der Qwen-2.1-Workflow lädt seine LoRAs vor dem Modell-Cache. Die bestehenden Stärken und Einstellungen bleiben erhalten. Alte Familienzuordnungen behalten dieselbe Wirkung: Qwen 2.1 allein oder alle vier Edit-2511/Rapid-Modelle. Beim Speichern der neuen Häkchen wird die konkrete Auswahl je Datei hinterlegt; der Ordner wird anschließend nicht mehr für diese Datei ausgewertet.

Die Einbindung verwendet ComfyUI `LoraLoaderModelOnly`: Die LoRAs verändern das Bildmodell; Textencoder und VAE bleiben erhalten. Die Erkennung ist eine Dateiliste, keine Prüfung der Modellkompatibilität. Verwende nur LoRAs, die für die ausgewählten Modelle trainiert wurden und deren Format ComfyUI unterstützt. Qwen Image 2.1 hat eine andere Architektur als Edit 2511; die Dateien sind nicht austauschbar. Beschleunigungs-/Distillations-LoRAs mit speziellen Sigmas, Schritten oder Guidance benötigen ihren eigenen Workflow; der automatische Loader passt den Sampler nicht selbst an.

## Bestehende Installation aktualisieren

Nach Übernahme der Quelldateien in deinen Checkout:

```bash
cd ~/image-studio
mkdir -p models/loras
bash studio-compose.sh up -d --build --no-deps web discord
bash studio-compose.sh logs --tail=80 web discord
```

ComfyUI muss bereits laufen. Falls Discord einen neuen Slash-Command noch nicht zeigt, Discord mit Ctrl+R neu laden und einen neuen Command beginnen.

Das aktualisierte Paket `qwen-edit-2511.zip` enthält einen Installer mit Vorprüfung, Quellcode-Sicherung und Restore. Er übernimmt ausschließlich den geprüften Repository-Stand und die zuvor gezeigten manuellen LoRA-Blöcke in `build_rapid_workflow`. Erkannte manuelle Stärken werden als Startwerte in `web/lora_defaults.json` übertragen. Andere lokale Codeänderungen stoppen die Installation vor dem Schreiben. Entferne deshalb bekannte manuelle Blöcke nicht vor der Vorprüfung, wenn ihre Stärken übernommen werden sollen.

## Speicherung, Setup und Serverumzug

Das Setup erstellt `models/loras/`. Individuelle LoRA-Dateien musst du selbst hineinlegen; sie werden nicht vom Modell-Setup heruntergeladen. Ein neues Deployment erhält nach dem Start dieselbe automatische Erkennung.

`web/lora_defaults.json` enthält Startwerte für die erstmalige Initialisierung. Danach liegen die tatsächlichen Einstellungen in der vorhandenen SQLite-Datenbank unter `data/`; ein Neubau, Neustart oder erneutes Setup überschreibt sie nicht. Änderungen erfolgen über die Adminseite. Für einen bestehenden Serverumzug übernimm insbesondere `data/`, `models/loras/` und die ursprüngliche `.env`, zusätzlich zu den sonstigen in der README beschriebenen Anwendungsdaten.

## Prüfung

Die automatisierten Tests prüfen Workflow-Verkettung, beide Rapid-Versionen, Text/Bildbearbeitung, Prompt-Erweiterung, neu hinzukommende Dateien, persistente Stärken, Admin-/CSRF-Schutz sowie Website- und Discord-Aufträge. Die per-Modell-Tests prüfen zusätzlich das Filtern vor der Generierung, leere Auswahlen, mehrere erlaubte Modelle, alte Einstellungen, ungültige/stale Formulare sowie die echte Discord-Job-API. Ein tatsächlicher GPU-Lauf mit deinen LoRA-Dateien erfolgt auf deinem Server.

## Beispiele für gezielte Auswahl

| Gewünschtes Verhalten | Häkchen bei Apply to models |
| --- | --- |
| Nur mit AIO v23 anwenden | ausschließlich Qwen Rapid AIO v23 — NSFW |
| Nur mit BF16 anwenden | ausschließlich Qwen Image Edit 2511 — BF16 |
| Mit beiden 2511-Präzisionen anwenden | FP8 Mixed und BF16 |
| Mit Qwen 2.1 anwenden | ausschließlich Qwen Image 2.1 |
| Für keinen Auftrag verwenden | alle Modell-Häkchen entfernen oder Enabled deaktivieren |

Die Stärke gilt gemeinsam für alle angehakten Modelle einer Datei. Mehrere LoRAs können auf dasselbe Modell eingeschränkt werden. Website und Discord filtern die aktiven Dateien anhand der tatsächlichen `model`-Auswahl des Auftrags. Ein bereits laufender Auftrag behält seine geladene Konfiguration.
