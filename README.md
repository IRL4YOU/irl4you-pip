# IRL4YOU PIP / IRL4YOU BOX

**Webseite:** [irl4you.de](https://irl4you.de) · **Discord:** [Community beitreten](https://discord.gg/nrBCEarMup) (Fragen, Fehler, Ideen)

**Version 0.9.14 (Beta).** Getestet auf einer Radxa ROCK 5B+ mit BELABOX-Image und DJI Osmo Action 4, Action 5 Pro und
Action 6. Vier Kameras gleichzeitig (Hauptbild und drei kleine Bilder) liefen ohne Frame-Drops bei rund 13 Mbit/s; die
Box war dabei zu etwa 70 % im Leerlauf. Noch kein Langzeittest über mehrere Stunden mit dem aktuellen Stand.
Auf der Orange Pi 5 Plus (frisches BELABOX-Image) ist die Installation getestet und der Überlagerungs-Baustein mit künstlichen
Testbildern geprüft, noch nicht mit Kameras.

Eigenständiges Zusatzpaket für eine BELABOX, **getrennt von der Original-Oberfläche**. Es ändert keine
BELABOX-Dateien, damit BELABOX-Updates weiter möglich bleiben. Eigene Weboberfläche mit Anmeldung über das
vorhandene BELABOX-Passwort (Rückfall: eigenes Passwort).

## Was geht

- **Status:** Meldungen, CPU je Kern, Takt, Temperatur, RAM und Upload je verbundener Netzwerkkarte samt Summe (Ethernet, WLAN, USB-/Mobilfunk-Router).
- **RTMP-Kameras:** neue Streams werden automatisch erkannt; Kameras lassen sich umbenennen, Rollen zuweisen.
- **DJI-Kameras per Bluetooth** (Protokoll nach Moblin, MIT): Suche, Koppeln, WLAN und RTMP-Ziel übergeben, Start. Pro Kamera
  Auflösung, fps, Bitrate und Stabilisierung. Ein eigener Dienst (`pipbox-dji`) hält die Verbindungen, verbindet nach
  Ausfällen neu, überwacht, ob der Stream ankommt, folgt Änderungen der Box-Adresse im Kameranetz und heilt einen hängenden
  Bluetooth-Adapter.
- **SRTLA-Serverliste:** mehrere Server speichern und per Auswahl umschalten (Stream-ID wird nie angezeigt).
- **Pipeline:** eine Kamera oder Bild-in-Bild mit bis zu drei kleinen Bildern (vier Kameras), Ecke und Größe wählbar, Ton von
  jeder Kamera. Die kleinen Bilder lassen sich in einer Vorschau frei verschieben (oder als Ecke wählen). Ein kleiner eigener GStreamer-Baustein (`gst/`) schreibt die kleinen Bilder in einem Durchgang direkt in
  das Hauptbild. Fällt eine Kamera aus, schaltet die Box nach 5 Sekunden automatisch auf die übrigen um.
- **Gleichlauf:** Verzögerung für Hauptbild und jedes kleine Bild per Regler (0 bis 3000 ms), bei laufender Sendekette ohne
  Neustart änderbar. Zum Abgleichen liegt eine Stoppuhr unter `tools/stopwatch.html`.
- **Ausgangswerte (frei änderbar):** Hauptbild 1080p/30 fps/8 Mbit/s, kleine Bilder 720p/30 fps/4 Mbit/s (nach der Rolle in
  der Pipeline), gleiche Stabilisierung bei allen Kameras, Hauptbild um 450 ms verzögert (Schätzung).
- **Live gehen / beenden:** eigene Sendekette (`belacoder` + `srtla_send`) als getrennter Root-Dienst mit strenger Prüfung
  aller Werte. Der gebaute `srtla_send` ist ein **latenzbewusster Patch** auf BELABOX/srtla (siehe `srtla/`, AGPL-3.0): er
  misst Laufzeit und Jitter je Leitung und verhindert so den Bitrate-Einbruch bei Leitungen mit unterschiedlicher Laufzeit.
  Verteilung wählbar: beste Leitung bevorzugen (Standard) oder alle gleichzeitig.
- **WLAN / Hotspot als Sendeweg:** Netze suchen, verbinden, trennen und als Sendeweg wählen, direkt in der Oberfläche
  (Root-Helfer mit festen Aktionen; das Passwort wird von diesem Projekt nicht gespeichert, NetworkManager legt es im WLAN-Profil ab).
- **Box ausschalten:** Herunterfahren und Neu starten direkt in der Oberfläche (Root-Helfer mit fester Liste, Protokoll wird vorher sauber geschlossen).
- **Fernzugriff (freiwillig):** Über Tailscale von unterwegs, nur im privaten Netz, nie öffentlich. Einrichten direkt in der
  Oberfläche; Anleitung: [ANLEITUNG-Fernzugriff.md](ANLEITUNG-Fernzugriff.md).
- **Software-Update:** In der Oberfläche nach neuen Versionen suchen und installieren. Die letzten 5 Versionen bleiben
  gesichert; man kann gezielt auf eine Version wechseln, auch auf eine ältere (gesichert oder als Release `vX.Y.Z` auf
  GitHub). Nicht während einer Übertragung.
- **System-Updates** der BELABOX über einen getrennten Root-Helfer mit festen Aktionen.
- **Stabile Bitrate über gebündelte Mobilfunkleitungen:** Der Encoder bekommt einen toleranteren Regler (kleiner Patch auf BELABOX/belacoder, siehe
  `belacoder/README.md`), damit die Bitrate nach einer kurzen Überlast wieder hochkommt. **Wichtig für den Empfänger:** Die Wartegrenze für
  Verlustmeldungen (`SRTO_LOSSMAXTTL`) sollte klein bleiben (BELABOX empfiehlt 10 bis 50, bei uns gemessen gut: 150). Ein großer Wert (zum Beispiel 600)
  lässt die Bestätigungen Sekunden lang ausbleiben und der Encoder senkt die Bitrate.
- **Protokolle, in zwei Stufen** (Karte "Protokolle: Speicherkarte schonen"). *Sparsam* (Standard bei neuen Installationen): Journal
  und Zustandsprotokoll nur im Arbeitsspeicher, die Speicherkarte wird geschont, nach einem Absturz oder Stromausfall bleibt aber
  keine Spur. *Ausführlich* (zur Fehlersuche): Journal dauerhaft (30 MB/7 Tage) und Zustandsprotokoll
  (`/var/log/pipbox-health.log`, alle 10 Sekunden, höchstens 4 MB), damit nach einem Totalausfall sichtbar bleibt, was kurz
  davor los war. Boxen, die vor 0.9.13 installiert wurden, bleiben beim Update auf "ausführlich".

## Was noch fehlt oder ungetestet ist

- Langzeitstabilität. Es gab unerklärte Totalausfälle der Box (zuletzt zwei in der Nacht zum 2. Oktober 2026, ohne
  Fehlermeldung im Protokoll). Verdacht: Stromversorgung, wenn ein USB-Router am USB-C-Port der Box hängt; nicht bewiesen.
  Seitdem lief die Box über 15 Stunden ohne neuen Ausfall. Auf der Orange Pi 5 Plus (gleiche Stromversorgung) lief der aktuelle Stand am 3. Oktober 2026 mehrere Stunden ohne Ausfall; auf der ROCK 5B+ schaltet sich die Box gelegentlich von selbst aus, Ursache unbekannt.
- Pocket 3 und weitere DJI-Modelle: Protokoll vorhanden, nie mit echter Kamera getestet. Die Action 6 lief; eine neue
  oder zurückgesetzte Kamera muss im Kopplungsmodus sein und die Kopplungsabfrage bestätigen.
- Der WLAN-Weg lief mit einem Handy-Hotspot (nur Mobilfunk, Verbinden mit Passwort über die Oberfläche) und der Verteilung
  "alle Leitungen gleichzeitig" nur kurz (rund 40 ms Laufzeit, einige Mbit/s); nicht unterwegs und nicht über Stunden.

Siehe [KONZEPT.md](KONZEPT.md) und [CHANGELOG.md](CHANGELOG.md).

## Ansehen ohne Box (Demo-Werte)

```sh
python3 server.py --demo
```

Dann `http://127.0.0.1:8780/` öffnen.

## Installation auf der Box

Voraussetzung: eine BELABOX mit dem BELABOX-Image (getestet: Radxa ROCK 5B+ und Orange Pi 5 Plus), Internet auf der Box und ein
Terminal auf der Box (SSH oder Tastatur). Nicht während einer Übertragung installieren.

```sh
cd /tmp
wget -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz
tar xzf irl4you-pip.tar.gz
cd irl4you-pip-main
sudo sh install/install.sh
```

Fehlt `wget`, geht auch `curl -L -o irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz`.
Die Installation lädt fehlende Pakete nach und baut den Bild-in-Bild-Baustein und den SRTLA-Sender selbst; das kann einige
Minuten dauern.

Danach im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden. Hat die BELABOX noch kein
Passwort, verlangt die Seite einen Setup-Code, den die Box in der Datei `/var/lib/pipbox/setup-code` bereithält
(`sudo cat /var/lib/pipbox/setup-code`); dann ein eigenes Passwort festlegen. Spätere Versionen spielt die Karte
"Software-Update" in der Oberfläche ein, ein erneutes Installieren ist nicht nötig.

Das Paket schreibt nach der Installation sehr wenig auf die Speicherkarte ("Protokolle: sparsam"). Für die Fehlersuche lässt
sich in der Karte "Protokolle" die ausführliche Stufe einschalten.

Rückweg: `sudo sh install/install.sh uninstall`.
Die Deinstallation entfernt Dienste und Programme. Liegen bleiben der Zustand (`/var/lib/pipbox`), die Sicherungen
(`/var/lib/pipbox-backup`), die Protokolle (`/var/log/pipbox-*.log`) und der Benutzer `pipbox`. Die Journal-Einstellung
und `/etc/pipbox` werden entfernt.

Optional, **nicht automatisch installiert** (`install/optional/`, nur für den Aufbau des Entwicklers auf der ROCK 5B+):
`pipbox-net.service` hält die feste Zweitadresse 192.168.80.50 für ein Kameranetz an `eth1`, und
`80-pipbox-no-internal-bt.rules` schaltet das eingebaute Bluetooth-Modul ab, damit nur ein USB-Bluetooth-Stick genutzt wird.
Der Knopf für **System-Updates** installiert echte Systemupdates (Kernel, BELABOX-Pakete) und kann die Box nach einem
Stromausfall unbrauchbar machen. Nur ohne laufende Übertragung und mit stabiler Stromversorgung benutzen.

## Software-Update

Die Oberfläche vergleicht ihre `VERSION` mit der Datei auf GitHub (`IRL4YOU/irl4you-pip`, Zweig `main`). Das Einspielen
macht ein getrennter Root-Helfer (`pipbox-swupdate`). Er lädt nur von dieser festen Adresse per HTTPS, prüft das Archiv
streng (nur normale Dateien, keine Pfade nach außen, Größe begrenzt, Python- und Installationsskript fehlerfrei,
Versionsnummer neuer), sichert die jetzige Version, führt `install.sh` aus und rollt bei einem Fehler automatisch
zurück. Wie bei jedem Update wird dabei Code aus dem Repository als root ausgeführt: Vertraue also dem Repository.

## Sicherheit

Zugangsdaten (BELABOX-Passwort, SRTLA-Stream-ID, WLAN-Daten der Kamera) liegen nur auf der Box in `/var/lib/pipbox` mit
eingeschränkten Rechten und gehören nicht in dieses Repository. Root-Helfer nehmen nur feste Stichworte an und prüfen
alles erneut.

## Lizenz

MIT, siehe [LICENSE](LICENSE) und [NOTICE.md](NOTICE.md) (enthält die Lizenz von Moblin, dessen DJI-Protokoll hier
nachgebaut wurde). **Ausnahme:** Der Ordner `srtla/` (Patch auf BELABOX/srtla und der damit gebaute Sender) steht unter
AGPL-3.0, wie das Original.
