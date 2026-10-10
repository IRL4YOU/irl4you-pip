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
   Technisch lauscht die Oberfläche auf allen Schnittstellen (`--host 0.0.0.0`), nimmt aber nur Anfragen von Loopback (Tailscale-Proxy), privaten
   Adressbereichen, Link-Local, 100.64.0.0/10 und IPv6-ULA an (`--allow-public` hebt das auf). **Unverschlüsselt:** Im Netz, über das die Box sendet (z. B. ein
   fremdes WLAN), laufen Anmeldung und Sitzung über HTTP im Klartext; dort die Oberfläche nur über Tailscale (HTTPS) benutzen (Issue #25). Der Schalter "Über fremde WLANs sperren" (Karte Verbindungen, Standard aus) trennt Verbindungen auf Gast-WLANs; Ethernet, eigener Hotspot, USB und Tailscale bleiben.
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
| `server.py` + `web/` | Oberfläche (eine Seite, 14 Sprachen zur Laufzeit), Auslastung, Kameras, Bildaufbau, SRTLA und WLAN, Updates, Fernzugriff, Ausschalten, Twitch (Anmeldung per Geräte-Code, Chat lesen und schreiben, Moderation, Ereignisse über EventSub, Akku-Warnung mit Bot-Konto), Hinweise bei Ausfällen |
| `pipbox_send.py` | Sendekette: startet Encoder und Sender, wählt bei Kameraausfall automatisch eine andere Anordnung, liest Netzänderungen live |
| `dji_daemon.py` | DJI-Kameras per Bluetooth koppeln und Stream starten, Verbindung je Kamera wählbar (Protokoll nach Moblin, MIT; Bibliothek bleak) |
| `hdmi_daemon.py` | HDMI-Eingang als Kamera "HDMI" einspeisen (Bild und Ton von der Aufnahmekarte) |
| `pipbox_live.py`, `pipbox_always.py` | Live-Steuerung der Sendekette und die Engine für "Alle Kameras immer bereit" (Compositor mit festen Bildfeldern) |
| `pipbox_watch.py` | Wächter der Sendekette (Issue #51): erkennt Hänger von belacoder (Statistik steht, Threads im Zustand D, Kamera ohne Bild im Mischer), sichert ein Diagnosepaket und startet belacoder neu |
| `pipbox_preview.py`, `install/pipbox-preview.{socket,service}` | Vorschau des gesendeten Bildes (Issue #52): ein eigener Dienst (Socket-Aktivierung, nur CAP_NET_RAW, endet nach einer Minute ohne Zuschauer) liest die SRT-Datenpakete mit, die belacoder lokal an srtla_send schickt (Kernel-Filter, Ordnung der Pakete), dekodiert mit dem Hardware-Dekoder, kodiert klein als H.264 und liefert ein fragmentiertes MP4 (selbst geschrieben, `Fmp4`) über einen Unix-Socket, Ausweichlösung Motion-JPEG; der Webserver (Benutzer pipbox, ohne neue Rechte) reicht es an den Browser durch; höchstens 10 Minuten, mit Häkchen im Browser dauerhaft |
| `install/pipbox-netaddr.py` | Feste Zusatzadresse (LAN): Root-Helfer (Auslösedatei wie bei den anderen Helfern) setzt eine zusätzliche Adresse an die Netzkarte mit einer bestimmten MAC-Adresse und legt ein Skript in `/etc/network/if-up.d` an, das sie bei jedem Hochfahren der Karte erneuert |
| `install/pipbox-btdriver.py`, `install/pipbox-wlandriver.py` | Treiber-Helfer: Bluetooth-Sticks (Realtek, Barrot) und der USB-WLAN-Stick AIC8800D80 werden beim Einstecken eingerichtet (eingeschränkte Rechte, nie während einer Übertragung) |
| `controllers.py`, `controller_keys.py`, `phone_battery.py` | Bluetooth-Controller koppeln und Tasten zuordnen, Akkustand gekoppelter Handys lesen |
| `install/setup.sh` | Komplett-Einrichtung: Grundsystem aktualisieren (Fortschrittsbalken), `install.sh` mit Schrittzeilen, Ergebnis; Protokoll in `~/install-ausgabe.txt` |
| `dji.py` | Bluetooth-Sticks und -Adapter: was steckt, was BlueZ kennt, Hinweise |
| `gst/gstpbpip.c` | GStreamer-Plugin: Bild-in-Bild-Mischer (je kleinem Bild Beschnitt, Deckkraft, Rahmen), Zwischenspeicher für die kleinen Bilder, Live-Verzögerung |
| `srtla/` | Patch auf BELABOX/srtla (AGPL-3.0): Laufzeit und Jitter je Leitung |
| `belacoder/` | Patches auf BELABOX/belacoder (GPL-3.0): Kamera-Zweige, toleranterer Regler, Statistik, Stall-Wächter |
| `bluetooth-src/` | Kernel-Quellen (GPL-2.0) für den Bluetooth-Treiber, damit weitere USB-Sticks erkannt werden |
| `install/` | Installation, Root-Helfer, systemd-Dateien, Zustandsprotokoll |
| `tools/` | Tests (ohne Box lauffähig), die Stoppuhr zum Einstellen der Kameraversätze und die Prüfung der Übersetzungen |
| `anleitungen/` | Anleitungen für Anwender (Kameras, GoPro, Senden, Twitch, Betrieb); dazu `ANLEITUNG-Fernzugriff.md` |

## Offene Punkte

- Langzeittest unterwegs im Freien (zu Hause liefen 47,5 Stunden am Stück ohne Absturz) und Gegenprobe auf der ROCK 5B+ (früheres gelegentliches Ausschalten von selbst, Ursache unbekannt).
- Frische Installation auf weiteren Boxen und Images (am 10. Oktober 2026 lief `setup.sh` auf einer frischen Karte einer Orange Pi 5 Plus fehlerfrei; die Namen der Netzwerkkarten (`eth0`, `eth1`) können auf einer neuen Karte vertauscht sein).
- Feinabstimmung der Mindestbitrate für schwankende Mobilfunkleitungen.

Anleitungen und weitere Themen: [irl4you.de](https://irl4you.de), zur BOX [irl4you.de/irl4you-box.html](https://irl4you.de/irl4you-box.html).
