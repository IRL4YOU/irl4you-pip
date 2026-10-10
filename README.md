# IRL4YOU PIP / IRL4YOU BOX

**Webseite:** [irl4you.de](https://irl4you.de) · **Discord:** [Community beitreten](https://discord.gg/nrBCEarMup) (Fragen, Fehler, Ideen)

**Version 0.9.227 (Beta).** Zusatzpaket für eine BELABOX mit eigener Weboberfläche (Port 8780): mehrere Kameras zu **einem** Bild mischen, über mehrere Leitungen senden,
Twitch-Chat und Fernzugriff, ohne die Original-Oberfläche anzufassen. Sie läuft **getrennt** davon.

> **Beta heißt:** Es läuft im Alltag, aber nicht alles ist lange und unterwegs geprüft (siehe „Stand“). Neue Versionen kommen oft; zurück auf eine frühere geht in der Oberfläche.

## Installation

Voraussetzung: BELABOX-Image (getestet: Orange Pi 5 Plus, Radxa ROCK 5B+), Internet und ein Terminal auf der Box (SSH oder Tastatur). **Nicht während einer Übertragung.**

1. **BELABOX-Passwort:** Hat die Box noch keins, zuerst in der BELABOX-Oberfläche (`http://<Adresse der Box>/`) eines festlegen. Die Oberfläche dieses Pakets nutzt dasselbe Passwort.
2. **Installieren** (ein Befehl auf der Box: lädt das Paket, aktualisiert das Grundsystem mit Fortschrittsbalken, installiert und meldet am Ende „Alles fehlerfrei installiert“; Protokoll in `~/install-ausgabe.txt`):

```sh
cd /tmp && rm -rf irl4you-pip-main irl4you-pip.tar.gz && wget -q -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz && tar xzf irl4you-pip.tar.gz && cd irl4you-pip-main && sudo sh install/setup.sh
```

   Ohne Systemupdate: `sudo sh install/setup.sh --ohne-systemupdate`. Bringt das Update einen neuen Kernel mit, sagt das Skript am Ende, dass neu gestartet werden soll.
   Klassisch ohne Systemupdate und mit voller Ausgabe: Archiv wie oben entpacken und `sudo sh install/install.sh` ausführen.
3. **Anmelden:** Im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden („Angemeldet bleiben“ gilt 30 Tage).

Spätere Versionen spielt die Karte **Software-Update** ein, neu installieren ist nicht nötig. Rückweg: `sudo sh install/install.sh uninstall` (Zustand, Sicherungen und Protokolle bleiben liegen).
Das Paket ändert nur **eine** Einstellung des RTMP-Servers der BELABOX (mit Sicherung und Rückweg), sonst keine BELABOX-Dateien, damit deren Updates weitergehen.
Weitere Details: [ANLEITUNG-Betrieb.md](anleitungen/ANLEITUNG-Betrieb.md).

## Anleitungen

- [Kameras anschließen](anleitungen/ANLEITUNG-Kameras.md) (RTMP, DJI, HDMI, USB) und [GoPro](anleitungen/ANLEITUNG-GoPro.md)
- [Senden](anleitungen/ANLEITUNG-Senden.md) (SRTLA, Sendewege, Bildaufbau, Live, Vorschau)
- [Twitch-Chat, Moderation und Akku-Warnung](anleitungen/ANLEITUNG-Twitch-Chat.md)
- [Betrieb und Pflege](anleitungen/ANLEITUNG-Betrieb.md) (Update, Sicherung, Protokolle, Fehler melden)
- [Fernzugriff über Tailscale](ANLEITUNG-Fernzugriff.md)
- [Hardware](anleitungen/ANLEITUNG-Hardware.md) (Bluetooth- und WLAN-Stick, WLAN der Kameras)
- Aufbau der Software: [KONZEPT.md](KONZEPT.md) · Änderungen: [CHANGELOG.md](CHANGELOG.md), ältere in [CHANGELOG-Archiv.md](CHANGELOG-Archiv.md)

## Was es kann

**Kameras**
- **RTMP-Kameras** (Handy-Apps, GoPro) werden automatisch erkannt; **QR-Code** für IRL Pro, Moblin und GoPro. **DJI-Kameras per Bluetooth** koppeln und starten (Protokoll nach Moblin); Verbindung je Kamera wählbar.
- **HDMI-** und **USB-Webcam** als Kamera. **Akkustand** aller DJI-Kameras mit Ladesymbol 🔌; „Nur Akkustand lesen“ auch für Kameras, die selbst senden (zum Beispiel Osmo Pocket 3).
- **Kamera-Ampel** und Hinweise bei Ausfällen (Kamera weg, USB-Gerät getrennt, Strom prüfen).

**Bild und Senden**
- **Bild-in-Bild** mit bis zu vier Kameras (Ecke, Größe, Beschnitt, Rahmen, Verzögerung je Bild), **Hauptbild wählen**, **Vorschau** des gesendeten Bildes, Fußleiste für das Handy.
- **„Alle Kameras immer bereit“** (Beta, Standard aus): Kameras kommen und gehen, ohne dass die Sendung neu startet.
- **SRTLA** über mehrere Leitungen (latenzbewusster Sender, Mindestanteil je Weg), **WLAN/Hotspot** als Sendeweg, Netzwerkkarten folgen der **MAC-Adresse**, automatisch live nach dem Start.

**Twitch**
- **Chat** mit Emotes, Abzeichen und Ereignissen; **Moderation** und Befehle (`/vip`, `/mod`, `/raid` …). Je Browser ein Konto: Streamer, **Moderator** oder **Zuschauer** mit eigener Anmeldung.
- **Akku-Warnung** einer DJI-Kamera im Chat.

**Betrieb**
- **Software-Update** und **System-Updates** in der Oberfläche, **Sicherung** (verschlüsselt) und Einspielen, **Protokolle**, **Fernzugriff** (Tailscale), **SSH** auf Knopfdruck.
- **Schutz:** nur private Netze und Tailscale erreichen die Oberfläche; die **Vorschau von außen ist aus**, weil sie den Upload der Sendung braucht (zu Hause erlaubbar, dann klein).
- **Problem melden / Wunsch äußern** (bereitet ein GitHub-Formular vor), 14 **Sprachen**, hell/dunkel, Streammodus.

## Stand

- **Getestet** auf einer Orange Pi 5 Plus mit vier DJI-Kameras gleichzeitig (zwei **Osmo Action 4**, **Action 5 Pro**, **Action 6**) und einer **GoPro Hero 8**: Hauptbild und drei kleine Bilder bei etwa 11 bis 13 Mbit/s,
  **47,5 Stunden am Stück** im Heimnetz (6. bis 8. Oktober 2026), frisch geflashte Karte mit `setup.sh` und eingespielter Sicherung.
- **Zusätzlich mit echter Kamera geprüft:** die **DJI Osmo Pocket 3** (sendet per RTMP an die Box; Akkustand und Ladezustand kommen per Bluetooth dazu).
  Auf der ROCK 5B+ nur ein älterer Stand (0.9.10).
- **Noch nicht geprüft:** lange Fahrten unterwegs; drei Mobilfunkwege nur kurz (40 s), Starlink gar nicht; „Alle Kameras immer bereit“ nur einige Stunden; weitere DJI-Modelle, GoPro Hero 9 bis 13;
  Twitch-Rollen und Befehle nur gegen Testserver (echt bestätigt sind Anmeldung und Testnachricht); Controller und Handy-Akku nur mit Testwerten; „feste Zusatzadresse“ nur mit Tests.
- **Offen:** unerklärte Totalausfälle einer Box (zuletzt in der Nacht zum 2. Oktober 2026, ohne Fehlermeldung; Verdacht Stromversorgung, nicht bewiesen); Überblendung beim Wechsel des Hauptbilds ist geplant.
- **Sprachen:** außer Deutsch maschinell übersetzt, nicht von Muttersprachlern geprüft (`web/i18n/README.md`).

## Ansehen ohne Box (Demo)

```sh
python3 server.py --demo
```

Dann `http://127.0.0.1:8780/` öffnen (das Passwort ist in der Demo schon eingetragen). Sie läuft nur auf dem eigenen Rechner.

## Sicherheit und Update

Zugangsdaten (BELABOX-Passwort, Stream-ID, WLAN-Daten) liegen nur auf der Box in `/var/lib/pipbox` mit eingeschränkten Rechten und gehören nicht ins Repository. Root-Helfer nehmen nur feste Stichworte an.
Die Oberfläche läuft im Netz, über das die Box sendet, **unverschlüsselt (HTTP)**: dort nur über Tailscale (HTTPS) benutzen oder „Über fremde WLANs sperren“ einschalten.
Das **Software-Update** lädt nur von `IRL4YOU/irl4you-pip` (Zweig `main`) per HTTPS, prüft das Archiv streng, sichert die jetzige Version und rollt bei Fehlern zurück.
Dabei läuft Code aus dem Repository als root: Vertraue also dem Repository.

## Danksagung

Ein besonderer Dank gilt **Bittersweet1987** (GitHub): Ideen und Fehlerberichte von Anfang an, die Engine für **„Alle Kameras immer bereit“** (Compositor im belacoder, aus seinem Projekt streamingbox),
viele Pull Requests (Protokolle, Menüs, Designs, Pulsanzeige, Übersetzungen) und die Vorlage für den **DJI-Dienst** (siehe [NOTICE.md](NOTICE.md)).
Danke auch an alle, die Protokolle und Rückmeldungen geschickt haben, an das Projekt **Moblin** (Erik Moqvist, DJI-Protokoll, MIT), an **BELABOX** und an die Discord-Community.

## Lizenz

MIT, siehe [LICENSE](LICENSE) und [NOTICE.md](NOTICE.md). **Ausnahmen:** `srtla/` (AGPL-3.0, wie das Original), `belacoder/` (GPL-3.0, wie das Original, darunter der Patch von Bittersweet1987) und die
Kernel-Quellen in `bluetooth-src/` (GPL-2.0). Einzelheiten in [NOTICE.md](NOTICE.md).
