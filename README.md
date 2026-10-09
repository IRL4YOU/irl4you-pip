# IRL4YOU PIP / IRL4YOU BOX

**Webseite:** [irl4you.de](https://irl4you.de) · **Discord:** [Community beitreten](https://discord.gg/nrBCEarMup) (Fragen, Fehler, Ideen)

**Version 0.9.173 (Beta).** Zusatzpaket für eine BELABOX mit eigener Weboberfläche: Kameras (RTMP, DJI per Bluetooth, HDMI-Eingang), Bild-in-Bild mit bis zu vier Kameras, Hauptbild wechseln, "Alle Kameras immer bereit" (Beta), Upload über mehrere Leitungen (SRTLA), Fernzugriff über Tailscale, Twitch-Chat mit Anmeldung und Moderation, Vorschau des gesendeten Bildes, Software-Update, 14 Sprachen, Ansicht für das Handy und mehr. Es läuft **getrennt von der Original-Oberfläche** der BELABOX.

> **Beta heißt:** Es läuft im Alltag, aber noch nicht alles ist über lange Zeit und unterwegs geprüft (siehe "Was noch fehlt oder ungetestet ist"). Neue Versionen gibt es oft; zurück auf eine frühere Version geht in der Oberfläche.

## Installation auf der Box

Voraussetzung: eine BELABOX mit dem BELABOX-Image (getestet: Radxa ROCK 5B+ und Orange Pi 5 Plus), Internet auf der Box und ein
Terminal auf der Box (SSH oder Tastatur). Nicht während einer Übertragung installieren.

**Schritt 1: BELABOX-Passwort.** Hat die BELABOX noch kein Passwort (frisches Image), zuerst in der BELABOX-Oberfläche
(`http://<Adresse der Box>/`) eines festlegen. Die Oberfläche dieses Pakets meldet sich mit demselben Passwort an; einen eigenen Setup-Code gibt es nicht. Mit dem Haken "Angemeldet bleiben" bleibt die Anmeldung 30 Tage bestehen, auch über Updates hinweg.

**Schritt 2: Installieren.** Auf der Box im Terminal:

```sh
cd /tmp
wget -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz
tar xzf irl4you-pip.tar.gz
cd irl4you-pip-main
sudo sh install/install.sh
```

Die Installation lädt fehlende Pakete nach und baut den Bild-in-Bild-Baustein und den SRTLA-Sender selbst; das kann einige
Minuten dauern.
Dazu gehört die Bluetooth-Bibliothek `bleak` für den DJI-Dienst (per `pip`, braucht Internet). Gelingt das nicht, bricht die
Installation mit einer Meldung ab, bevor etwas verändert wurde.

**Schritt 3: Anmelden.** Im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden. Ist in Schritt 1
noch kein Passwort gesetzt worden, steht auf der Seite, dass es zuerst in der BELABOX-Oberfläche festgelegt werden muss; die Seite
wartet darauf und zeigt die Anmeldung dann von selbst.

Spätere Versionen spielt die Karte "Software-Update" in der Oberfläche ein, ein erneutes Installieren ist nicht nötig.

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

## Anleitungen

- [Kameras anschließen](anleitungen/ANLEITUNG-Kameras.md) (RTMP, DJI, HDMI) und [GoPro](anleitungen/ANLEITUNG-GoPro.md)
- [Senden](anleitungen/ANLEITUNG-Senden.md) (SRTLA, Sendewege, Bildaufbau, Live)
- [Twitch-Chat und Akku-Warnung](anleitungen/ANLEITUNG-Twitch-Chat.md)
- [Betrieb und Pflege](anleitungen/ANLEITUNG-Betrieb.md) (Update, Sicherung, Protokolle)
- [Fernzugriff über Tailscale](ANLEITUNG-Fernzugriff.md)
- Übersicht und Aufbau: [KONZEPT.md](KONZEPT.md)

## Stand und Test

Getestet auf einer Orange Pi 5 Plus (BELABOX-Image) mit vier DJI-Kameras (zwei Osmo Action 4, Action 5 Pro, Action 6) gleichzeitig: Hauptbild und drei kleine Bilder bei rund 11 bis 13 Mbit/s. Ein Dauertest über gut acht Stunden am 4. Oktober 2026 lief mit allen vier Kameras
ohne Aussetzer und ohne Neustart der Sendekette, nachdem das Kamera-WLAN auf WPA2 und 5 GHz umgestellt war (siehe "Hinweise zum Kamera-WLAN"). Am 6. Oktober 2026 wurde eine **frisch aufgespielte Box** mit dem Installationsbefehl unten eingerichtet (Installation, Update, Tailscale, vier Kameras, Twitch-Warnung liefen). Mit der neuen Engine für "Alle Kameras immer bereit" und vier Kameras im Bild lag die Auslastung bei etwa 18 bis 21 Prozent (vorher 26 bis 30 Prozent im normalen Modus mit zwei Kameras im Bild); über drei Mobilfunkwege lag der Anteil an Neuübertragungen in einem kurzen Test (40 Sekunden) bei etwa 1,6 Prozent. Ein längerer Fahrtest unterwegs steht noch aus.
Auf der Radxa ROCK 5B+ wurde nur ein älterer Stand getestet (0.9.10).

Das Paket ändert nur eine Einstellung des RTMP-Servers der BELABOX (Leerlaufgrenze für Kameras, mit Sicherung und Rückweg; ein kleiner apt-Haken stellt sie nach einem Update des BELABOX-Pakets wieder her) und sonst keine
BELABOX-Dateien, damit BELABOX-Updates weiter möglich bleiben. Eigene Weboberfläche mit Anmeldung über das
vorhandene BELABOX-Passwort (nur ohne belaUI, etwa in der Entwicklung, gilt ein eigenes Passwort).

## Was geht

- **Status:** Drei Kästen nebeneinander. *System*: CPU (Gesamtwert und höchster Kern), Temperatur, Lüfter und Arbeitsspeicher. *Kameras*: je Kamera Name,
  **Ampel** und aktuelle Eingangsbitrate. *Upload*: je verbundener Netzwerkkarte Datenrate samt **Ampel** und Summe (Ethernet, WLAN, USB-/Mobilfunk-Router). Warnungen
  erscheinen darüber; sind keine da, bleibt der Platz leer.
- **RTMP-Kameras:** neue Streams werden automatisch erkannt; Kameras lassen sich umbenennen. Jede Kamera (zum Beispiel ein Handy) kann über eine eigene Verbindung der Box senden, etwa einen zweiten Router, um die Last zu verteilen; die angezeigte Adresse gilt dann für diese Verbindung.
  **QR-Code für Kamera-Apps:** Unter "Kamera hinzufügen" und bei jeder RTMP-Kamera: App wählen (IRL Pro, Moblin, GoPro) und "QR-Code erzeugen"; die Kamera-App übernimmt die Verbindungsdaten. Mit Moblin und IRL Pro geprüft; GoPro (Labs-Firmware) mit einer Hero 8 geprüft (läuft im Dauerbetrieb).
- **DJI-Kameras per Bluetooth** (Protokoll nach Moblin, MIT): Suche, Koppeln, WLAN und RTMP-Ziel übergeben, Start, mit Statusmeldungen je Schritt.
  **Je Kamera wählbare Verbindung** aus den vorhandenen Verbindungen der Box: WLAN-Hotspot und WLAN-Netze mit Name und Passwort aus NetworkManager, alle anderen
  (Ethernet, USB-Router, Modem) mit einmal eingetragenem und gespeichertem WLAN der Kamera. Pro Kamera Auflösung, fps, Bitrate und Stabilisierung.
  Ein eigener Dienst (`pipbox-dji`, Bibliothek `bleak`) hält die Verbindungen, verbindet nach Ausfällen neu, überwacht, ob der Stream ankommt, und
  verbindet mehrere Kameras nacheinander (gleichzeitig bricht auf dem Funkchip ab).
- **Alle Kameras immer bereit (Beta, Schalter im Bildaufbau bei "Bild in Bild", standardmäßig aus):** Jedes Bildfeld hat immer einen Eingang. Kameras können jederzeit dazukommen, ausfallen und zurückkehren, ohne dass die Sendung neu startet; ohne Kamera sendet die Box Schwarz und Stille. Fällt die Hauptkamera aus, übernimmt die nächste, kehrt die erste zurück, wird sie wieder Hauptbild. Tauschen, Größe, Ecke, Ton, Stumm und Verzögerung gehen im Betrieb. Fußleiste und Tonwahl zeigen nur Kameras mit Bild. Technik: Seit 0.9.114 die **Compositor-Engine von Bittersweet1987** (eine feste Pipeline mit vier Kamera-Zweigen, belacoder-Patch `belacoder/belacoder-live-feeds.patch`, GPL-3.0, Steuerung `pipbox_live.py`); fehlt ein belacoder mit `-sb10` oder neuer, läuft ersatzweise die frühere Zubringer-Variante (`pipbox_always.py`, braucht `gst-launch-1.0`). Fehlt beides, läuft die Sendung im normalen Modus weiter und die Karte "Live" nennt den Grund. Bekannte Grenzen (laut Bittersweet1987): beim Tauschen steht das Bild etwa 1 Sekunde, der DJI-Ton hat bei stoßweiser Ankunft 1 bis 2 Sekunden Lücken.
- **DJI: Akku, Ladeanzeige, Verbindung:** Der Akkustand aller DJI-Kameras steht im Status; das Steckersymbol 🔌 zeigt "am Ladekabel" bei der Osmo Action 4, der Osmo Action 5 Pro und der Osmo Action 6 (bei anderen Modellen unbekannt). Beim Einrichten gibt es **Netze in der Nähe** zur Auswahl. Die gewählte Verbindung (zum Beispiel der mobile Router) merkt sich die Box über die Hardware-Adresse der Netzwerkkarte und wechselt nicht ins Heimnetz, auch wenn sich Netzwerknamen (`eth0`, `eth1`) nach einem Neustart vertauschen; ist sie nicht da, gibt es einen klaren Fehler.
- **SRTLA-Serverliste:** mehrere Server speichern und per Auswahl umschalten (Stream-ID wird nie angezeigt).
- **Pipeline:** eine Kamera oder Bild-in-Bild mit bis zu drei kleinen Bildern (vier Kameras), Ecke und Größe wählbar, Ton von
  jeder Kamera. Die kleinen Bilder lassen sich in einer Vorschau frei verschieben (oder als Ecke wählen). Je kleinem Bild lassen sich Skalierung (1 bis 100 %), Ein-/Ausblenden, Beschnitt (Pixel links, rechts, oben, unten, bezogen auf 1920 x 1080), Eckenrundung und ein Rahmen (Dicke, Farbe, Deckkraft) einstellen; die Vorschau zeigt es sofort. Das Hauptbild liest der Baustein dafür nur dort, wo etwas durchscheinen muss. Ein kleiner eigener GStreamer-Baustein (`gst/`) schreibt die kleinen Bilder in einem Durchgang direkt in
  das Hauptbild. Fällt eine Kamera aus, schaltet die Box automatisch auf die übrigen um (das dauert etwa 5 Sekunden ohne Bild) und nimmt die Kamera erst nach 60 Sekunden stabilem Signal wieder auf.
- **Hauptbild wählen:** Bei Bild-in-Bild steht in der Live-Karte unter "Hauptbild" ein Schalter mit den Kameras, die im Bild sind. Die aktuelle Hauptkamera ist hervorgehoben, ein Klick auf eine andere macht sie zum Hauptbild und tauscht die beiden (Kamera und Verzögerung bleiben beisammen, Ecke, Größe und Ton bleiben am Platz). Läuft die Sendung, startet der Encoder dafür neu und das Bild ist etwa 5 Sekunden unterbrochen. **Experimentell:** Wählt man im Bildaufbau "Hauptbild tauschen ohne Unterbrechung" (Hauptbild und erstes kleines Bild, oder alle Kameras), läuft der Tausch ohne Neustart des Encoders als harter Schnitt, Ton inklusive. Dafür dekodiert die Box jede Kamera der Gruppe zweimal (groß und klein, bei vier Kameras 6 statt 4, bei "alle" 8 Dekodierungen). Im Heimnetz mit vier DJI-Kameras geprüft (mehrere Tausche in Folge, ohne Neustart und ohne Einbruch im Upload); noch nicht über längere Zeit und unterwegs. Die Kameras der Gruppe sollten dieselbe Auflösung senden. Eine Überblendung ist geplant.
- **Fußleiste am Handy:** Bei Bild-in-Bild stehen unten (Breite bis 620 Pixel) "Live"/"Stop", ein Knopf je Kamera und der Ton-Knopf in einer Reihe. Kamera: kurzer Druck macht sie zum Hauptbild, Doppeltipp blendet das kleine Bild aus oder wieder ein (der Ton bleibt), langer Druck deaktiviert sie (nicht im Stream, springt bei Ausfall nicht als Ersatz ein), nochmal lang aktiviert sie wieder. Ton: kurzer Druck wechselt die Tonspur, langer Druck schaltet stumm. Ausblenden und Stumm gehen ohne Neustart der Sendung, Tonspur und Tausch ohne Neustart nur mit "Tausch ohne Unterbrechung". Mit Testquellen auf der Box geprüft, **noch nicht mit echten Kameras**.
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
- **Details im Status:** zugeklappt, auf Wunsch: Senden (Bitrate, RTT, Sendepuffer, Neuübertragungen, Paketverlust, Encoder-Bilder), Prozessor (je Kern, GPU, NPU), Temperaturen, Speicherplatz.
- **Erreichbarkeit und Sicherheit:** Die Oberfläche (Port 8780) nimmt nur Anfragen aus privaten Netzen, Loopback und Tailscale an, öffentliche Adressen werden abgewiesen. Begrenzt sind Verbindungen (64 gesamt, 16 je Absender), Wartezeit (15 s) und gleichzeitige Passwortprüfungen (2); die Sperre nach 5 Fehlversuchen greift auch bei gleichzeitigen Anmeldungen. **Im Netz, über das die Box sendet (z. B. fremdes WLAN), ist die Verbindung unverschlüsselt (HTTP):** Dort die Oberfläche nur über Tailscale (HTTPS) benutzen. Wer das nicht will, schaltet in der Karte "Verbindungen" den Schalter "Über fremde WLANs sperren" ein (Schutz vor dem Aussperren eingebaut).
- **Über fremde WLANs sperren** (Karte "Verbindungen", Bereich "Zugriff auf diese Oberfläche"): Schalter, der den Zugriff über die Netze sperrt, über die die Box sendet (zum Beispiel ein fremdes WLAN), weil die Verbindung dort unverschlüsselt ist (HTTP). Der Zugriff über Tailscale (HTTPS) und das Heimnetz bleibt.
- **Fernzugriff (freiwillig):** Über Tailscale von unterwegs, standardmäßig nur im privaten Netz (nur Geräte in Ihrem Tailscale-Konto). Einrichten direkt in der
  Oberfläche; Anleitung: [ANLEITUNG-Fernzugriff.md](ANLEITUNG-Fernzugriff.md). Wer auch **ohne Tailscale-App** von überall zugreifen will, kann auf
  ausdrücklichen Knopfdruck **Funnel** einschalten (öffentlich im Internet, nur durch das BELABOX-Passwort geschützt, rote Warnung, bleibt bis zum Beenden an, auch nach einem Neustart).
- **Software-Update:** In der Oberfläche nach neuen Versionen suchen und installieren. Die letzten 5 Versionen bleiben
  gesichert; man kann gezielt auf eine Version wechseln, auch auf eine ältere (gesichert oder als Release `vX.Y.Z` auf
  GitHub). Nicht während einer Übertragung. Ein **gelber Punkt in der Kopfleiste** zeigt, wenn eine neuere Version da ist. Ein zweiter Punkt zeigt, wenn **Systemupdates** der BELABOX bereitliegen; dafür sucht die Box nach jedem Start und danach alle 6 Stunden still nach (nie während einer Übertragung). Ohne Updates sind beide Punkte aus.
- **System-Updates** der BELABOX über einen getrennten Root-Helfer mit festen Aktionen. Die Box sucht kurz nach jedem Start und danach alle 6 Stunden von selbst nach Updates (nur die Paketliste, nie während einer Übertragung); der gelbe Punkt "System" in der Kopfleiste zeigt, wenn welche bereitliegen.
- **Automatisch live gehen** nach dem Start der Box (Schalter in der Live-Karte, standardmäßig aus): einmal pro Start, sobald eine Kamera sendet.
- **Streammodus** (Knopf im Kopf der Seite und in der Live-Karte): blendet Adressen, Namen und Protokolle aus, wenn der Bildschirm mitgefilmt wird. Die Einstellung merkt sich nur der Browser.
- **Mindestanteil je Sendeweg** (bei "Alle Leitungen gleichzeitig nutzen"): Jeder geeignete Weg bekommt mindestens 10 Prozent der Pakete, damit auch ein schwächerer Weg (Mobilfunk neben DSL, Starlink neben 5G) warm bleibt und bei einem Ausfall des besten nicht erst anlaufen muss. Siehe `srtla/README.md`.
- **Kamera-Ampel:** Der Punkt vor jeder Kamera in der Kameraliste zeigt den Zustand auf einen Blick. **Grün**: sendet und ist im Bild. **Gelb**: sendet, ist aber noch nicht oder nicht mehr im
  Bild (zum Beispiel beim Wiederverbinden: eine zurückgekehrte Kamera wird erst nach 60 s stabilem Signal wieder aufgenommen, damit eine wackelige Kamera nicht dauernd den Encoder neu startet).
  **Rot**: kein Signal. **Grau**: Status unbekannt. Beim Darüberfahren erscheint eine kurze Erklärung. Die Ampel steht auch in der Karte "Status" (Name, Punkt und aktuelle Eingangsbitrate), damit man die Kameraliste nicht öffnen muss.
- **Ampel für die Sendewege** (Kasten "Upload" in der Karte "Status"): **Grün** = der Weg trägt Pakete, **Gelb** = verbunden, aber in Reserve (Laufzeit zu hoch oder unruhig), **Rot** = nicht verbunden oder kein Netz, **Grau** = keine Sendung.
- **Stabile Bitrate über gebündelte Mobilfunkleitungen:** Der Encoder bekommt einen toleranteren Regler (kleiner Patch auf BELABOX/belacoder, siehe
  `belacoder/README.md`), damit die Bitrate nach einer kurzen Überlast wieder hochkommt, und einen Stall-Wächter, der nur den Ausgang prüft (ein kurzer Aussetzer einer kleinen Kamera beendet die Sendung nicht mehr). Auch die Einstellungen des Empfängers (SRT-Latenz, Umordnungstoleranz) beeinflussen die Bitrate.
- **Entwickler** (Karte "Entwickler"): SSH-Zugang mit einem Knopf ein- und ausschalten, das SSH-Passwort anzeigen und zurücksetzen, wie in der Original-Oberfläche
  der BELABOX. Der Schalter startet und beendet nur den SSH-Dienst; beim ersten Einschalten wird bei Bedarf ein zufälliges Passwort erzeugt. Schlüssel und
  Einstellungen von SSH bleiben unberührt.
- **WLAN-Hotspot** (Karte "Verbindungen", unter jeder WLAN-Karte): Schalter "Hotspot-Modus" An/Aus und "Einstellen" (Name, Passwort, 2,4 oder 5 GHz, Kanal). Macht
  aus dem Stick ein eigenes WLAN der Box, zum Beispiel für DJI-Kameras oder ein Handy; die Kamera übernimmt Name und Passwort selbst. Mit NetworkManager umgesetzt;
  **mit echtem Stick und einer GoPro Hero 8 geprüft** (am 8. Oktober 2026 sendet die GoPro über den Hotspot der Box, Adresse `10.42.0.x`, seit über einer Stunde ohne Unterbrechung mit etwa 3 Mbit/s); mit DJI-Kameras über den Hotspot gesondert nicht gemessen.
- **Einstellungen sichern und einspielen** (Karte "Einstellungen sichern"): Kameras, Bildaufbau, SRTLA-Server, DJI-Kameras, HDMI-Eingang, Akku-Warnung im Twitch-Chat (ohne Token), Optionen der Oberfläche (Menüs), Hotspots, WLAN-Netze u. a. in einer Datei.
  Mit Passwörtern immer **mit Passwort verschlüsselt** (AES-256). Einspielen nur ohne Sendung, der Stand davor wird gesichert. WLAN-Netze und DJI-Kameras **noch nicht mit einer echten Box geprüft**.
- **USB-Webcam als Quelle** (neu, Beta, gleicher Abschnitt, Schalter "Quelle"): Eine Kamera, die sich als Webcam (UVC) meldet, zum Beispiel eine Action-Kamera im Webcam-Modus, wird wie die HDMI-Kamera als Kamera "USB-Kamera" eingespeist (Bildformat und Ton werden selbst gewählt). Die Osmo Action 6 wird erkannt (Kamera, Mikrofon, Formate); **ein Bild kam bei meinem Test noch nicht an**, deshalb ungeprüft. Anleitung: [ANLEITUNG-Kameras.md](anleitungen/ANLEITUNG-Kameras.md).
- **HDMI-Kamera** (Karte "Kameras", Abschnitt "HDMI- und USB-Kameras"): Der HDMI-Eingang der Box (z. B. eine DJI Action 5 per USB-C-HDMI-Kabel) wird als Kamera "HDMI" in die Box eingespeist (Hardware-Kodierung, H.264) und ist wie jede Kamera wählbar: Hauptbild, kleines Bild, Notbetrieb. Schalter "Als Kamera senden", Bitrate, Bildrate und Ton (HDMI-Ton oder ohne) einstellbar. Eigener Dienst `pipbox-hdmi` (root), startet bei Signal und nach Ausfall von selbst. **Mit einer echten Action 5 nur als Probelauf geprüft** (Befehlskette und der Dienst aus einem Temp-Ordner: etwa 40 % eines Kerns bei 1080p60 auf 30 fps, 30 fps am Ausgang), nicht als eingerichteter Dienst und nicht über die Oberfläche; Verzögerung ungemessen. USB-Kameras fehlen noch.
- **Chat** (Karte "Chat"): Anmeldung bei Twitch per **Geräte-Code** (Code und Link, kein Passwort und kein Token eintippen; die Zugangsdaten bleiben auf der Box). Der Chat läuft über die gewählten Sendewege der Box und liest mit Emotes von Twitch, 7TV, BetterTTV und FrankerFaceZ und mit Abzeichen. Karten für Sub, Geschenk-Abos, Raid, Cheer und Ankündigungen; mit zusätzlichen Rechten auch **Follows** und **Kanalpunkte** (EventSub, Schalter "Ereignisse einschalten", nur eigener Kanal). Schreiben im Chat (über die Twitch-Schnittstelle mit Rückmeldung, sonst IRC), **Moderation** (Nachricht löschen, Timeout, Sperre; getrennt ein- und ausschaltbar), eine Amplitude für die Verbindung und Optionen hinter dem Zahnrad. Alle Texte des Chats bleiben so, wie die Zuschauer sie schreiben (nie übersetzt). **Anmeldung, Testnachricht und das Bot-Konto (Anmeldung und Schreiben im richtigen Kanal) hat der Entwickler auf einer echten Box bestätigt; Ereignisse, Senden über die Twitch-Schnittstelle und Moderation sind bisher nur gegen Testserver geprüft.** Anleitung: [ANLEITUNG-Twitch-Chat.md](anleitungen/ANLEITUNG-Twitch-Chat.md).
- **Hinweise bei Ausfällen** (Kopf der Karte "Status", mit "i" für die Erklärung): eine Kamera hat gesendet und ist weg, der HDMI-Eingang hat kein Signal oder einen Fehler, ein USB-Adapter ist ausgefallen oder wieder da (Auswertung der Kernelmeldungen). Bei Anzeichen von zu wenig Strom steht "Bitte Stromversorgung prüfen". Die Box hat keinen Messfühler für die Eingangsspannung; das ist ein Hinweis, keine Messung.
- **Akku-Warnung im Twitch-Chat** (Karte "Kameras", Bereich "DJI-Kameras"): Fällt der Akku einer per Bluetooth verbundenen DJI-Kamera unter die Schwelle (Standard 10 %), schreibt ein Bot eine Nachricht in den Chat (Twitch-IRC wie NOALBS, Token mit dem Recht "chat:edit"). Kanal und Bot-Konto sind getrennt (das Bot-Konto kann ein zweites Konto sein), die Nachricht ist einstellbar ({Kamera}, {Prozent}), mit Test-Knopf. **Wer schreibt:** ein **Bot-Konto**, das du per Klick anmeldest (zweite Anmeldung per Geräte-Code, nur die Rechte `chat:read chat:edit`, eigene Datei `twitch-bot-login.json`); ohne Bot schreibt das **angemeldete Hauptkonto**, und ohne beides gilt der von Hand eingetragene Token. Der Chat selbst (Lesen, Schreiben, Moderation) bleibt immer beim Hauptkonto. Ist der Chat nur für Follower oder Abonnenten, muss der Bot Moderator oder VIP sein. Der Token liegt nur auf der Box (`twitch.json`, Rechte 0600) und nicht in der Einstellungssicherung. Mit echtem Twitch geprüft: Anmeldung und Testnachricht, die **Warnung bei einem wirklich niedrigen Kamera-Akku** (Test am 6. oder 7. Oktober 2026, vor dem Bot-Konto) und das Schreiben über ein Bot-Konto im richtigen Kanal (Testnachricht). Eine echte Akku-Warnung über das Bot-Konto ist noch nicht gesehen. Eine Kamera nur am HDMI-Kabel meldet keinen Akku.
- **Nur Akkustand lesen** (DJI-Kamera, Schalter in ihrer Karte): Für eine Kamera, die per HDMI sendet, liest die Box nur per Bluetooth den Akkustand (Status, Twitch-Warnung). Mit einer echten Action 5 (HDMI) geprüft: Der Akkustand kommt per Bluetooth.
- **Sprachen** (beim ersten Öffnen und im Kopf der Seite; Hell und Dunkel mit Sonne/Mond im Kopf): Englisch (Standard), Deutsch, Französisch, Spanisch, Portugiesisch (Brasilien), Italienisch, Niederländisch, Polnisch, Türkisch, Russisch, Chinesisch (vereinfacht), Japanisch, Koreanisch, Thai. Texte, Datum und Uhrzeit folgen der Sprache. **Außer Deutsch maschinell übersetzt, nicht von Muttersprachlern geprüft.** Verbessern oder neue Sprache: `web/i18n/README.md`.
- **Protokolle herunterladen** (Karte "Protokolle", Knopf "Protokolle herunterladen"): eine Textdatei mit den Meldungen der Box für die Fehlersuche
  oder ein GitHub-Issue. Passwörter, Stream-ID, Servername, WLAN-Namen sowie IP- und MAC-Adressen werden vorher durch Platzhalter ersetzt (vor dem Weitergeben
  trotzdem kurz durchsehen).
- **Protokolle, in zwei Stufen** (Karte "Protokolle"). *Sparsam* (Standard bei neuen Installationen): Journal
  und Zustandsprotokoll nur im Arbeitsspeicher, die Speicherkarte wird geschont, nach einem Absturz oder Stromausfall bleibt aber
  keine Spur. *Ausführlich* (zur Fehlersuche): Journal dauerhaft (30 MB/7 Tage) und Zustandsprotokoll
  (`/var/log/pipbox-health.log`, alle 10 Sekunden, höchstens 4 MB), damit nach einem Totalausfall sichtbar bleibt, was kurz
  davor los war. Boxen, die vor 0.9.13 installiert wurden, bleiben beim Update auf "ausführlich".

## Hinweise zum Kamera-WLAN (aus dem Betrieb)

DJI-Kameras setzen im WLAN gelegentlich für einige Sekunden mit den Daten aus. Was in unserem Aufbau (GL.iNet-Router mit Mobilfunk, vier Kameras, Orange Pi 5 Plus) geholfen hat:

- **Nur 5 GHz, WPA2, 20 MHz Kanalbreite:** Nach der Umstellung von WPA3 auf WPA2 gab es über Stunden keinen Aussetzer mehr (vorher bei einer Kamera etwa einen pro Minute). Ein Kanalwechsel allein
  (44 auf 36 und zurück) brachte nichts. WPA2 mit AES und einem langen Passwort ist sicher genug für ein reines Kameranetz.
- **Kanal 36 bis 48 (kein DFS):** Die Osmo Action 5 Pro unterstützt im 5-GHz-Band laut Datenblatt nur 5150 bis 5250 und 5725 bis 5850 MHz, also nicht die DFS-Kanäle in der Mitte. Nach unserem Kenntnisstand (bitte selbst prüfen) sind in Deutschland 36 bis 64
  nur für den Innenbereich freigegeben und 149 bis 165 für private WLANs nicht.
- **RTMP-Leerlaufgrenze:** Der RTMP-Server der BELABOX wirft eine Kamera, die 4 Sekunden lang nichts schickt, aus dem Bild (`drop_idle_publisher 4s`); jedes Mal startet der Encoder dann neu. Dieses Paket setzt
  die Grenze auf 15 Sekunden (mit Sicherung `99-belabox-rtmp.conf.vor-pipbox`) und stellt sie nach einem Update des BELABOX-Pakets per apt-Haken wieder her. Die Aussetzer der Kamera selbst beseitigt das nicht, sie
  laufen nur durch, ohne dass etwas neu startet.
- **Sendewege nicht im Kamerafunk:** Beobachtung vom 6. Oktober 2026 (nicht bewiesen): Lag der Hotspot eines Handys als Sendeweg auf demselben 5-GHz-Kanal wie das Kamera-WLAN, stiegen Laufzeitspitzen und Neuübertragungen; mit einem zusätzlichen Weg über ein anderes Band (2,4 GHz) sanken sie wieder. Besser sind Sendewege **per Kabel** (USB-Tethering, Router per Ethernet) oder auf einem anderen Band.
- **Ampeln:** In der Karte "Status" zeigt die Ampel der Kameras, ob eine Kamera im Bild ist (grün), gerade wieder aufgenommen wird (gelb) oder fehlt (rot). Die Ampel der Sendewege zeigt, welcher Weg trägt (grün),
  in Reserve steht (gelb) oder fehlt (rot).

## Bluetooth-Stick für die DJI-Kameras

Die eingebauten Bluetooth-Module der Boxen empfangen schlecht, darum ist ein USB-Stick besser. Der Abschnitt "Bluetooth" in der Karte "Verbindungen" zeigt, welche Bluetooth-Sticks laufen (mit Name und USB-Kennung), ob ein Treiber gerade eingerichtet wird, und meldet einen Stick, aus dem der Kernel keinen Adapter macht (nicht unterstützt oder ohne Treiber).

| Stick | Chip | Stand |
|---|---|---|
| ASUS USB-BT500 (`0b05:190e`) | Realtek RTL8761B | **getestet**, läuft auf der Orange Pi 5 Plus (auch ohne Zusatztreiber) |
| TP-Link UB500 (`2357:0604`) | Realtek RTL8761BUV | der Kernel 5.10 kennt ihn nicht; **die Box richtet den Treiber beim Einstecken selbst ein** (siehe unten). **Noch nicht an der Box mit diesem Stick geprüft** |
| weitere Realtek-Sticks (`2550:8761`, `2c4e:0115` Mercusys MA530, `0bda:8771`, `0bda:a725`, `2b89:8761`) | Realtek RTL8761B | wie der UB500 (Treiber automatisch), nicht geprüft |
| UGREEN Bluetooth 5.4 und 6.0 (CM748, `33fa:0010`/`33fa:0012`) | BARROT BR8654/BR8554 | der Kernel 5.10 startet den Chip nicht von allein (er schickt ein Zufallsbyte zu viel, danach liegen alle Antworten verschoben); **die Box richtet den Treiber beim Einstecken selbst ein** (siehe unten). **Getestet** mit dem UGREEN BT6.0 (`33fa:0012`) an der Orange Pi 5 Plus, läuft als zweiter Adapter neben dem TP-Link |

**Automatischer Treiber für Realtek- und Barrot-Sticks.** Manche Realtek-Sticks kennt der Kernel 5.10 nicht in seiner Tabelle: Sie starten ohne Firmware, finden keine Kameras und wirken tot. Barrot-Sticks (UGREEN) brauchen keine Firmware, schicken aber ein Zufallsbyte zu viel; der Treiber enthält dafür die Prüfung des Linux-Kernels (Commit 7722d6fb54) und eine zweite für das einzelne Byte als eigenes USB-Paket. Steckt so ein Stick (Liste oben) beim Einstecken oder beim Start, baut die Box aus den mitgelieferten Original-Quellen des Kernels
(`bluetooth-src/`, v5.10.160, GPL-2.0, unverändert) das Modul `btusb` neu, mit zusätzlichen Kennungen, spielt es nach `/lib/modules/<Kernel>/updates/` ein und lädt es. Das dauert wenige Minuten. Dabei gilt:

- Gebaut wird nur auf dem passenden Kernel (5.10.160) und mit den vorhandenen Kernel-Headern; die Quellen werden vor dem Bau per SHA-256 geprüft, es wird nichts aus dem Internet geholt.
- **Nie während einer Übertragung** (das Neuladen trennt Bluetooth kurz); die Box wartet, bis die Übertragung beendet ist.
- Nach dem Laden muss ein Adapter da sein, das neue Modul wirklich laufen und die Firmware ohne Fehler laden; sonst wird alles zurückgerollt und für diese Kombination nicht noch einmal versucht. Es läuft dann mit dem Standardtreiber weiter. Das Standardmodul des Kernels wird nie überschrieben.
- Nach einem Kernel-Update gilt der Treiber nicht mehr; die Box prüft nach dem Start und alle 15 Minuten und richtet ihn, wenn für den neuen Kernel vorbereitet, neu ein. Deinstallation und "Rückweg": Datei `/lib/modules/<Kernel>/updates/btusb.ko` löschen (macht `install.sh uninstall`).
- Nach dem Wechsel auf einen anderen Stick fragt eine Kamera eventuell einmal nach der Kopplung (neue Bluetooth-Adresse).

## WLAN-Stick mit AIC8800D80 (UGREEN AX900 WiFi 6)

Der Kernel 5.10 der BELABOX hat keinen Treiber für diesen Chip. Der Stick meldet sich beim Einstecken zuerst als **USB-Laufwerk** (`a69c:5723`, "Aic MSC", mit dem Windows-Treiber darauf) und muss in den WLAN-Modus geschaltet werden. Die Box erledigt das beim Einstecken selbst, ohne dass jemand sudo braucht:

| Stick | Stand |
|---|---|
| UGREEN AX900 WiFi 6 (`a69c:5723`, Chip AIC8800D80) | **die Box richtet den Treiber beim Einstecken selbst ein** (dauert einige Minuten, **braucht dabei einmal Internet**). **Getestet** an der Orange Pi 5 Plus (blauer USB-3-Anschluss) mit dem ganzen Ablauf: Helfer holt und baut den Treiber, schaltet den Stick um, die WLAN-Schnittstelle (`wlan0`) entsteht und verbindet sich mit einem gespeicherten Netz; auch nach erneutem Einstecken und nach einem Neustart |

**Wie es geht.** Der Helfer `pipbox-wlandriver.py` (Dienst `pipbox-wlandriver`, ausgelöst beim Einstecken und vom Zeitgeber alle 15 Minuten) holt einmal den Treiber `shenmintao/aic8800d80` im **festen Stand** `1d1b8ff` (Zweig `legacy-mcu1`, GPL-2.0, mit der Firmware des Herstellers) von github.com, prüft ihn gegen eine feste SHA-256-Summe, baut daraus `aic_load_fw` und `aic8800_fdrv` für den Kernel, legt die Module nach `/lib/modules/<Kernel>/updates/aic8800/`, die Firmware nach `/lib/firmware/aic8800_fw/USB/aic8800D80/` und die Einstellung `aic_fw_path` nach `/etc/modprobe.d/pipbox-aic8800.conf`, schaltet den Stick mit `usb_modeswitch` um und prüft, dass eine WLAN-Schnittstelle entsteht. Danach laden udev und der Kernel die Module beim Einstecken von selbst. Die Meldung steht in der Karte "Verbindungen" im Abschnitt "WLAN-Verbindungen".

- **Wichtig:** Dieser Stand ("legacy-mcu1") ist für Chips mit `chip_mcu_id=1`. Die neuere Firmware (358072 Byte statt 327037 Byte) passt dort nicht in den Speicher des Chips; der Upload bricht bei Adresse `0x170400` mit "bin upload fail" ab. Das haben wir an der Box so gemessen.
- **Root-Rechte nur dafür:** Der Dienst läuft mit eingeschränkten Rechten (nur Module laden und Kernelmeldungen lesen, Schreiben nur in die genannten Ordner, Netz nur für diesen Download, keine neuen Rechte). Er nimmt keine Eingaben an.
- Gebaut wird nur auf dem passenden Kernel (5.10.160) mit vorhandenen Kernel-Headern und **nie während einer Übertragung**. Ohne Internet versucht die Box es später erneut; geht es nicht, wird alles zurückgebaut und für diese Kombination nicht noch einmal versucht.
- Rückweg: `sudo python3 /opt/pipbox/pipbox-wlandriver.py uninstall` (entfernt Module, Firmware und die Einstellung), oder die Regel `/etc/udev/rules.d/81-pipbox-wlandriver.rules` löschen.

## Was noch fehlt oder ungetestet ist

- **Langzeitstabilität:** Das Zustandsprotokoll der Box belegt **47,5 Stunden am Stück** (6. bis 8. Oktober 2026) mit vier DJI-Kameras und einer GoPro, mit zwei gewollten Neustarts am 7. Oktober (Neustart der Box von der Oberfläche aus) und ohne Absturz; der Entwickler berichtet von etwa drei Tagen (älteres Protokoll gibt es nicht mehr). Das lief zu Hause im Heimnetz. Noch nicht geprüft: lange Fahrten unterwegs im Freien. Frühere unerklärte Totalausfälle der Box (zuletzt zwei in der Nacht zum 2. Oktober 2026, ohne Fehlermeldung im Protokoll) sind nicht erklärt; Verdacht: Stromversorgung, wenn ein USB-Router am USB-C-Port der Box hängt, nicht bewiesen.
- Ungetestet: Pocket 3 und weitere DJI-Modelle (Protokoll vorhanden, nie mit echter Kamera). Eine neue oder zurückgesetzte Kamera muss im Kopplungsmodus sein und die Kopplungsabfrage bestätigen.
- Sprachen: nur im Browser mit Demo-Werten geprüft (Vollständigkeit, Zeilenumbrüche), nicht von Muttersprachlern und nicht auf echten Handys in jeder Sprache. Zusammengesetzte Texte können in einzelnen Fällen noch deutsch oder englisch bleiben. Die Sprachdateien werden einmal am Ende einer Reihe von Versionen nachgezogen.
- Mehrere Sendewege: Der Mindestanteil je Weg (10 Prozent bei "alle") und die Umordnungstoleranz 47 am Empfänger sind für **einen klar besseren Weg plus schwächere Zusatzwege** abgestimmt. Zu Hause (schneller DSL-Weg plus Mobilfunk) war das lange erprobt, über **drei Mobilfunkwege** gab es bisher nur einen kurzen Test (siehe "Stand und Test"). Starlink neben 5G ist nicht geprüft. Sind alle Wege gleich unruhig, steigen die Neuübertragungen.
- **Alle Kameras immer bereit** (Compositor-Engine) ist neu (0.9.114): bisher einige Stunden Betrieb mit vier Kameras, aber noch kein langer Lauf und keine Fahrt. Frühere Berichte über Abstürze einer Box mit der Zubringer-Variante (0.9.112) sind nicht geklärt; ob die neue Engine sie behebt, ist eine begründete Vermutung, nicht bewiesen. Der Schalter bleibt deshalb standardmäßig aus.
- **QR-Code für GoPro** (HERO 8 bis 13, Labs-Firmware nötig): Befehle wie auf der offiziellen GoPro-Labs-Seite. Mit einer **Hero 8** funktionieren sowohl die **GoPro-App** (Live, eigene RTMP-Adresse) als auch die **Labs-Codes** (seit den größeren Codes aus 0.9.125, am 8. Oktober 2026 im Dauerbetrieb bestätigt); **Hero 9 bis 13 sind nicht geprüft**. Voraussetzung: Die GoPro darf nicht zurückgesetzt sein, sie muss einmal mit der GoPro-App verbunden und das WLAN der Box dort eingerichtet worden sein. Einrichtung: [ANLEITUNG-GoPro.md](anleitungen/ANLEITUNG-GoPro.md). Fehlersuche: Protokolle mit den Abschnitten "Verbindungen am RTMP-Eingang" und "Hotspot der Box". Manche Scan-Apps erkennen den `larix://`-Link von IRL Pro nicht (dann Kamera-App des Handys oder Link in IRL Pro importieren).
- **HDMI-Kamera, Akku:** "Nur Akkustand lesen" ist nur mit der Osmo Action 5 (Pro) getestet, **nicht mit der Action 6**. Die Ladeanzeige 🔌 ist je Modell mit einem Versuch gemessen (Action 4, 5 Pro, 6); die Osmo 360 ist ausgenommen.
- Die Action 5 Pro und die Action 6 fallen im WLAN öfter aus als die beiden Action 4 (Ursache offen: Kamera, Firmware oder Funkumgebung).
- Geplant, nicht gebaut: Überblendung beim Wechsel zwischen Hauptbild und kleinem Bild (heute ein harter Schnitt). Eigene Empfangsprozesse je Kamera sind mit der Engine für "Alle Kameras immer bereit" erledigt. **GoPro** wird nicht von der Box gesteuert (kein Bluetooth, kein Akkustand): sie sendet als normale RTMP-Kamera, Einrichtung in [ANLEITUNG-GoPro.md](anleitungen/ANLEITUNG-GoPro.md).

Siehe [KONZEPT.md](KONZEPT.md), [CHANGELOG.md](CHANGELOG.md) und für ältere Versionen [CHANGELOG-Archiv.md](CHANGELOG-Archiv.md).

## Ansehen ohne Box (Demo-Werte)

```sh
python3 server.py --demo
```

Dann `http://127.0.0.1:8780/` öffnen. In der Demo ist das Passwort schon eingetragen (einfach "Anmelden" klicken); sie läuft nur auf dem eigenen Rechner.

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

## Danksagung

Ein besonderer Dank gilt **Bittersweet1987** (GitHub). Ohne seine Mithilfe gäbe es dieses Paket nicht in dieser Form. Er hat

- von Anfang an mit Ideen, Wünschen und Fehlerberichten mitgeholfen (über zwanzig Issues, unter anderem Import und Export der Einstellungen, Protokolle, Hotspot, Entwickler-Menü, Fußleiste am Handy, Hell/Dunkel und Sprachen, Verbindungen benennen),
- die Engine für **"Alle Kameras immer bereit"** mit Compositor und den Kamera-Zweigen im belacoder beigetragen (Pull Request #27, aus seinem Projekt streamingbox), dazu den belacoder-Regler und die Bildkopie (#29, #41),
- weitere Pull Requests geschrieben: hochkantes Kamerabild als kleines Bild (#30), Reihenfolge der Kamera-Knöpfe (#33), umfangreiche Protokolle für die Fehlersuche (#36, #39, #40, #45), Verbindungen benennen (#37), neu geordnete Menüs, "Optionen", die Designs "Klar" und "Kompakt" und die Pulsanzeige in der Kopfleiste (#38, #42, #46, #48, #49) sowie Übersetzungen (#47),
- mit seinem **DJI-Dienst** die Vorlage für den Bluetooth-Dienst geliefert (siehe [NOTICE.md](NOTICE.md)),
- Fehlerprotokolle anderer Nutzer ausgewertet und alles auf seiner eigenen Box mit mehreren Kameras ausprobiert.

Danke auch an die Nutzer, die Protokolle und Rückmeldungen geschickt haben (zum Beispiel Swissi), an das Projekt **Moblin** (Erik Moqvist, DJI-Protokoll, MIT), an **BELABOX** und an alle in der Discord-Community.

## Lizenz

MIT, siehe [LICENSE](LICENSE) und [NOTICE.md](NOTICE.md) (enthält die Lizenzen von Moblin, dessen DJI-Protokoll hier
nachgebaut wurde, und vom DJI-Dienst, dessen Ablauf übernommen wurde). **Ausnahmen:** Der Ordner `srtla/` (Patch auf BELABOX/srtla und der damit gebaute Sender) steht unter
AGPL-3.0, wie das Original. Der Ordner `belacoder/` (Patches auf BELABOX/belacoder, darunter der Patch von Bittersweet1987 für die Kamera-Zweige) und das damit gebaute Programm stehen unter GPL-3.0, wie das Original; die Kernel-Quellen in `bluetooth-src/` unter GPL-2.0. Einzelheiten in [NOTICE.md](NOTICE.md).
