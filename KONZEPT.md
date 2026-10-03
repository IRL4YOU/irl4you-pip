# IRL4YOU BOX – Aufbau und Grundregeln

Eigenständiges Zusatzpaket für eine BELABOX (ROCK 5B+, Orange Pi 5 Plus). Es ändert keine Dateien der
BELABOX-Oberfläche (`belaUI`), damit BELABOX-Updates weiter möglich bleiben. Den aktuellen Stand und die Grenzen
des Tests beschreiben README und CHANGELOG (Beta).

## Grundregeln

1. **Getrennt von BELABOX.** Eigener Ordner `/opt/pipbox`, eigene systemd-Dienste, eigene Weboberfläche auf Port 8780.
2. **Leicht für die CPU.** Der Encoder (belacoder) und die Kameraeingänge brauchen die Reserve. Hardware-Decoder wo möglich,
   keine Dauerabfragen, Protokolle in der Größe begrenzt.
3. **Nur privat erreichbar.** Im lokalen Netz mit Anmeldung; von unterwegs ausschließlich über Tailscale (`tailscale serve`),
   niemals `funnel` (öffentlich).
4. **Geheimnisse bleiben auf der Box.** Stream-ID, WLAN-Passwörter und Zugangsdaten stehen nie in Antworten der Oberfläche,
   nie in Protokollen und nie im Repository. Einzige bekannte Ausnahme: `belacoder` bekommt die Stream-ID als
   Programmargument, sie ist für lokale Benutzer der Box in der Prozessliste sichtbar.
5. **Root nur über feste Helfer.** Die Oberfläche läuft als Benutzer `pipbox`. Alles, was Root braucht (Sendekette,
   Updates, Fernzugriff, WLAN, Ausschalten), erledigt ein kleiner Root-Helfer: Die Oberfläche legt im Zustandsordner eine
   Anforderungsdatei mit einem festen Stichwort (oder streng geprüftem JSON) ab, ein systemd-`.path` startet den Helfer,
   der die Datei ohne Verweise zu folgen liest, erneut prüft und ausführt.

## Datenfluss

Kameras (RTMP-Kameras oder DJI per Bluetooth/WLAN) → nginx-rtmp der BELABOX → `belacoder` mit einer Pipeline, die
`server.py` erzeugt (Hauptbild und bis zu drei kleine Bilder, Baustein `pbpipmix`, H.265) → `srtla_send` (gepatcht, latenzbewusst,
mehrere Netze gleichzeitig) → SRTLA-Server.

## Bausteine

| Baustein | Aufgabe |
|---|---|
| `server.py` + `web/` | Oberfläche, Auslastung, Kameras, Bildaufbau, SRTLA und WLAN, Updates, Fernzugriff, Ausschalten |
| `pipbox_send.py` | Sendekette: startet Encoder und Sender, wählt bei Kameraausfall automatisch eine andere Anordnung, liest Netzänderungen live |
| `dji.py`, `dji_daemon.py` | DJI-Kameras per Bluetooth koppeln und Stream starten (Protokoll nach Moblin, MIT) |
| `gst/gstpbpip.c` | GStreamer-Plugin: Bild-in-Bild-Mischer, Zwischenspeicher für die kleinen Bilder, Live-Verzögerung |
| `srtla/` | Patch auf BELABOX/srtla (AGPL-3.0): Laufzeit und Jitter je Leitung |
| `install/` | Installation, Root-Helfer, systemd-Dateien, Zustandsprotokoll |
| `tools/` | Tests (ohne Box lauffähig) und die Stoppuhr zum Einstellen der Kameraversätze |

## Offene Punkte

- Langzeittest über mehrere Tage und Gegenprobe auf der ROCK 5B+ (gelegentliches Ausschalten von selbst, Ursache unbekannt).
- Frische Installation auf weiteren Boxen und Images.
- Feinabstimmung der Mindestbitrate für schwankende Mobilfunkleitungen.
