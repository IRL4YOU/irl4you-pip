# PIPBOX – Konzept (ursprünglicher Entwurf vom 1. Oktober 2026)

Eigenständiges Zusatzpaket für die BELABOX (ROCK 5B+). Es ersetzt den alten
PIP-Aufbau unter `flash-pip/`, der nicht weiterentwickelt wird. **Dieser Text ist der ursprüngliche Entwurf
und in Teilen überholt** (Bausteine, die hier "offen" stehen, sind inzwischen gebaut). Den aktuellen Stand (Beta)
beschreiben README und CHANGELOG.

## Leitregeln

1. **Getrennt von BELABOX.** Eigener Ordner `/opt/pipbox`, eigener systemd-Dienst,
   eigene Weboberfläche und eigener Port. Keine Änderung an `belaUI`-Dateien.
   Ein BELABOX-Update darf PIPBOX nicht kaputt machen und umgekehrt.
2. **Leicht für die CPU.** Facecam höchstens 720p, möglichst Hardwaredecoder.
   Die alten Totalhänger (5–20 min) traten bei hoher CPU-Last auf.
3. **Erst Langzeittest, dann Komfort.** Vor jeder neuen Funktion 30–60 min Dauerlauf.
4. **Nur privat erreichbar.** Oberfläche nur auf `127.0.0.1`, Fernzugriff nur
   über Tailscale (`tailscale serve`), niemals `funnel`. Zusätzlich Passwort.

## Bausteine

| Baustein | Aufgabe | Stand |
|---|---|---|
| `server.py` + `web/` | Oberfläche, Auslastung, Ampelwarnungen, RTMP-Kameras anlegen/entfernen (`cameras.json`) | Rohbau, mit Demo-Werten lauffähig |
| Einspeiser (je Kamera) | RTMP lesen, Framerate in die Caps, selbständig neu verbinden, getaktet an die Hauptpipeline | offen |
| PIP-Pipeline | Hauptbild dekodieren, Facecam einblenden, H.265-Encoder | offen (Ausgangspunkt: Ringpuffer-Overlay aus `flash-pip/`) |
| DJI-Modul | Kameras per Bluetooth koppeln und Stream starten (Protokoll nach Moblin, MIT-Lizenz) | offen |
| Tailscale | Fernzugriff über privaten Link | Anleitung unten |
| Watchdog | Neustart bei Totalstillstand, falls Hardware-Watchdog nutzbar | zu prüfen |

## Übergabe an die BELABOX (Entscheidung am neuen Image)

- **Weg A (bevorzugt):** PIPBOX liefert das fertige Bild lokal (SRT/UDP) an eine
  Standard-Pipeline von BELABOX. Setzt voraus, dass das neue Image einen solchen
  lokalen Eingang hat. **Noch nicht geprüft.**
- **Weg B:** PIPBOX startet Encoder und `srtla_send` selbst. Unabhängig, aber
  Bonding und Bitratenanpassung müssen selbst gepflegt werden.

## DJI-Kameras automatisch einbinden (nach Moblin, MIT-Lizenz)

Quelle: `eerimoq/moblin`, Ordner `Moblin/Integrations/Dji/` (Lizenz laut GitHub
und LICENSE-Datei: MIT, Copyright Erik Moqvist). Beim Nachbau Urheberhinweis
und Lizenztext mitliefern.

Ablauf (Bluetooth-LE, Schreib-/Notify-Kanäle FFF4/FFF5):
1. Scannen: DJI-Geräte erkennbar an den Herstellerdaten (Firmenkennung AA08);
   Bytes 2-3 nennen das Modell (Action 2/3/4/5 Pro/6, Osmo 360, Pocket 3/4).
2. Verbinden, koppeln (feste Kopplungsnachricht), alten Stream stoppen.
3. Der Kamera WLAN-Name und -Passwort mitteilen, dann Auflösung, Bildrate,
   Bitrate, Codec und die RTMP-Adresse der Box senden. Die Kamera tritt dem
   WLAN bei und streamt selbst zur Box. Je Modell leicht andere Nachrichten.
4. Nachrichtenformat: 0x55, Länge, Version 4, CRC8, Ziel, ID, Typ, Nutzdaten, CRC16.

Folgen für uns:
- Die Box braucht WLAN-Daten, die sie der Kamera mitteilt. Das ist ein Geheimnis
  und muss geschützt gespeichert werden (nie im Log, nie in der Oberfläche).
- Das WLAN muss dasselbe Netz sein, in dem die Box erreichbar ist (Router-WLAN).
- Auf der Box fehlen Bluetooth-Programme; Installation braucht Freigabe.
- Der Bluetooth-Adapter teilt sich ein Funkmodul (RTL8852BE) mit dem WLAN, das
  schon bei BELABOX Probleme gemacht hat.
- Das Protokoll stammt aus Moblins Nachbau, nicht von DJI. Funktion pro Modell
  nur durch Test mit der echten Kamera belegbar.

## Kameras per RTMP

Kamera anlegen: Name, Rolle (Hauptbild / Bild-in-Bild / Weitere) und Schlüssel
(leer = automatisch). Die Oberfläche zeigt die RTMP-Adresse
`rtmp://<Box>:1935/publish/<Schlüssel>` für die Kamera. Live-Status kommt aus
der nginx-rtmp-Statistik (`--rtmp-stat-url`, XML). **Die Statistik-Adresse auf
dem neuen Image ist noch nicht ermittelt**; ohne sie steht „Status unbekannt“.
Angelegte Kameras steuern bisher noch keine Pipeline.

## Auslastungsanzeige

CPU je Kern, Takt, Temperatur, RAM, Upload je Weg (`eth0`, `eth1`), blockierte
Prozesse, später Kamera-fps und Encoder-Werte. Ampelgrenzen stehen in
`server.py` (`LIMITS`) und sind **Platzhalter** bis zu echten Messungen.
Später: begrenztes Verlaufslog als Ersatz für die alte Blackbox.

## Tailscale (Ablauf, am neuen Image gemeinsam durchführen)

1. Tailscale-Konto anlegen und Tailscale auf Handy/Laptop installieren.
2. Auf der Box Tailscale installieren und `tailscale up` ausführen. Der
   ausgegebene Anmeldelink wird vom Nutzer selbst im Browser bestätigt.
3. `tailscale serve --bg 8780` macht die Oberfläche unter dem
   `*.ts.net`-Namen der Box im privaten Netz erreichbar.
4. Rückweg: `tailscale serve reset`, `tailscale down`.

## Offene Fragen

- Welche DJI-Modelle? Hat das Image funktionierendes Bluetooth, sonst USB-Stick?
- Hauptkamera weiter RTMP oder künftig SRT?
- Gibt es auf dem neuen Image einen lokalen Eingang für Weg A?

## Bekannte Risiken aus dem alten Aufbau

- Totalhänger vermutlich auf Kernel-/SoC-Ebene (CPU-Takt-/Spannungsregelung).
- In-Place-Overlay in Decoderpuffer: mögliche Referenzbild-Verfälschung ungeprüft.
- Quelltext der funktionierenden Overlay-Version 0.4 liegt lokal nicht vor.

## Spätere Wünsche

- **Mehrere SRTLA-/SRT-Server speichern:** Server mit Name, Adresse und Port
  anlegen, im Dropdown wählen und dauerhaft speichern (die Original-BELABOX
  kennt nur eine Einstellung). Die gewählte Einstellung muss so an belaUI
  übergeben werden, dass dessen Dateien unverändert bleiben. Dazu zuerst
  prüfen, wie belaUI Relay-/Serverwerte speichert und setzt.
- **Netz der Kamera wählbar:** Kabel-Router (`eth0`), USB-Router wie GL.iNet (`eth1`) oder
  Hotspot der Box (`wlan0`). Die RTMP-Adresse für die Kamera richtet sich nach dem gewählten
  Netz (umgesetzt für die Auswahl; Hotspot selbst noch offen, BELABOX hat eine eigene Funktion).
- **Eigener RTMP-Eingang** auf eigenem Port (zweite nginx-Instanz), getrennt von BELABOX.
