# Änderungen

## 0.9.12 (Beta)
Sicherheit, Ressourcenverbrauch und Aufräumen nach einer unabhängigen Prüfung des Quelltexts.
- Sicherheit: Die Root-Helfer lesen ihre Anforderungsdateien im Ordner des Benutzers `pipbox` nur noch ohne Verweisen
  (Symlinks) zu folgen, nur bis 4 KB und nur normale Dateien; unbekannte Inhalte werden nicht mehr ins Protokoll geschrieben.
  Status- und Protokolldateien des Update-Helfers, die Verzögerungsdatei und der Arbeitsordner der Sendekette werden ohne
  Verweise geschrieben. Die Zahlenfelder des Bildaufbaus (Ecken, Größe, Position, Verzögerung) werden vor dem Erzeugen des
  Pipeline-Textes zu Zahlen in festen Bereichen gezwungen. Eine negative Anfragelänge wird abgelehnt.
  "WLAN vergessen/ersetzen" lässt das Profil des Kameranetzes unangetastet. Vorabversionen von GitHub werden nicht mehr
  als Wechselziel angeboten.
- Weniger Last: Die Seite fragt im Hintergrund-Tab nichts ab und zugeklappte Karten nur selten (DJI, Update, Netze, Fernzugriff,
  WLAN, Bildaufbau); die Abfragen überlappen nicht mehr. Der Server merkt sich teure Ergebnisse kurz: Netzadressen (3 s),
  Dienststatus und nginx-Statistik (1,5 s), Messwerte (1,5 s), die Paketliste (bis sich die Datei ändert) und tailscale (20 s).
  Die Sendekette liest die Netzliste nur noch alle 6 s statt alle 2 s.
- Weniger Schreiben auf die Speicherkarte: Das Zustandsprotokoll schreibt alle 10 s statt alle 2 s und erzwingt das Speichern
  nur alle 30 s (bei plötzlichem Stromausfall fehlen bis zu 30 s). Das Journal speichert alle 5 Minuten statt alle 5 Sekunden.
- Update-Prüfung bei GitHub höchstens alle 6 Stunden und nie während einer Übertragung (der Knopf "Suchen" fragt immer nach).
- Behoben: Stürzt `srtla_send` ab, startet es mit der Einstellung "alle Leitungen" wieder (die 300-ms-Spanne ging verloren).
  Fehlt der gepatchte Sender, wartet der Start nicht mehr 20 Sekunden umsonst.
- Doku: KONZEPT.md neu geschrieben (ohne überholte und interne Teile), Lizenzhinweis zu `srtla/` (AGPL-3.0), Hinweise zur
  Deinstallation und zu `install/optional/`, korrigierte Kommentare und Angaben (300 ms, PBKDF2, Port).
- Tests erweitert (Verweise in Anforderungsdateien, Zahlenprüfung, Arbeitsordner).

## 0.9.11 (Beta)
- Neu: **Sendemodus** (Knopf im Kopf der Seite und in der Live-Karte). Er blendet Adressen, Namen und Protokolle aus, die beim
  Filmen des Bildschirms nicht zu sehen sein sollen: SRTLA-Server (Adresse), Fernzugriff, WLAN-Name und Netzliste, DJI-Bluetooth-
  Adressen, Technik- und Update-Protokolle. Wird nur im Browser gemerkt. Private IP-Adressen der Netzwerkkarten bleiben sichtbar.
- Neu: **Hinweis "Noch nicht übernommen".** Einstellungen, die erst nach einem Neustart der Sendung wirken (SRTLA-Server,
  Bitrate, Latenz, Verteilung), werden erkannt: nach dem Speichern erscheint ein Fenster mit "Stream neu starten" / "Später",
  in der Live-Karte bleibt ein gelber Hinweis. Die Sendekette meldet dafür beim Start Prüfsummen ihrer Einstellungen (ohne Stream-ID).
- Neu: **Gesamt-Upload in der Live-Karte** (grün ab 1,5 Mbit/s, orange ab 0,5, sonst rot). Server-Name klein darunter statt Adresse.
- Neu: Fuß der Seite mit Version, Hinweis auf Beta, Lizenz, Webseite, Discord, Quellcode und Drittprojekten. Die Versionsanzeige oben entfällt.
- Neu: Die Karte "SRTLA und WLAN" ist geteilt in "SRTLA: Server, Bitrate und Latenz" und "Netze zum Senden und WLAN".
- Verbessert: **Netze zum Senden wirken sofort**, auch während der Sendung (srtla_send liest die Liste per Signal neu).
- Behoben: **Fehlalarm beim Start der Sendung.** Der Encoder startete eine Sekunde nach srtla_send und gab nach wenigen Sekunden
  auf (ein automatischer Neustart pro Start, Meldung "Verbindung abgebrochen"). Jetzt wartet er, bis ein Weg zum Server steht.
  Meldungen in der Live-Karte sind verständlicher und verschwinden nach drei Minuten.
- Behoben: **DJI "Kamera nicht mehr sichtbar".** Die Bluetooth-Suche läuft jetzt während des Verbindens weiter; veraltete Einträge
  werden früher vergessen.
- Behoben: Ein nicht mehr vorhandenes Netz (z. B. frühere eth1) blockierte alle Änderungen an den Sendewegen. Das Häkchen speichert
  nur noch die Netzauswahl und überschreibt keine anderen Einstellungen mehr (Mindestbitrate sprang zurück).
- Geändert: Virtuelle Netze (Tailscale, Docker u. ä.) erscheinen nicht mehr als Sendeweg. Bei "Alle Leitungen gleichzeitig" gilt
  300 statt 150 ms Spanne. Namen: "USB-Router (eth2)", "Bildaufbau" statt "Pipeline".
- Tests erweitert (Prüfsummen, Teilspeichern, Netzfilter).

## 0.9.10 (Beta)
- Neu: **Kleine Bilder frei verschieben.** In der Pipeline-Karte gibt es eine Vorschau, in der die kleinen Bilder mit Maus oder Finger
  an jede Stelle gezogen werden. Die Position wird als Promille des Verschiebewegs gespeichert (das Bild bleibt immer im Bild);
  die vier Ecken und "unten Mitte" bleiben als Voreinstellungen. Ecke "frei (verschiebbar)" braucht den neuen Überlagerungs-Baustein
  (wird beim Update automatisch neu gebaut). Eigene Umsetzung. Auf der Orange Pi 5 Plus mit
  künstlichen Testbildern an sechs Positionen auf den Pixel genau geprüft; mit echten Kameras noch nicht.
- Neu: **Box herunterfahren und neu starten** in der Oberfläche (Karte "Box ausschalten", mit Rückfrage und Warnung während einer
  Übertragung). Root-Helfer `pipbox-power.py` mit fester Liste (poweroff, reboot); schließt vorher das Protokoll sauber ab, damit nach
  dem Ausschalten nichts fehlt. Einschalten geht weiter nur über Strom oder Taste.
- Verbessert: **Installation auf einem frischen BELABOX-Image.** `install.sh` installiert fehlende Pakete (bluez, python3-dbus,
  python3-gi) und legt die Gruppe "bluetooth" an; vorher fehlten sie auf der Orange Pi.
- Geändert: Netzwerkkarten heißen nach ihrer Art ("Ethernet (eth0)", "USB-Netz, z. B. USB-Router (eth1)", "WLAN (wlan0)") statt nach
  festen Annahmen über die ROCK 5B+.
- Neu: Tests für freie Position, Ausschalter und die neuen Prüfungen.

## 0.9.9 (Beta)
- Behoben: **Ruckeln im Bild bei drei Kameras.** Zwei getrennte Einblend-Bausteine beschrieben das Hauptbild nacheinander
  (rund 3 verlorene Bilder pro Sekunde). Jetzt zeichnet ein einziger Baustein alle kleinen Bilder in einem Durchgang
  (`pbpipmix` mit `slot2/corner2/slot3/corner3`). Gemessen mit drei Kameras: 0 Frame-Drops bei ca. 13 Mbit/s.
  Der Überlagerungs-Baustein wird beim Software-Update automatisch neu gebaut.
- Neu: **Vierte Kamera** (drittes kleines Bild) mit eigener Ecke und eigener Verzögerung. Die Automatik zum Umschalten
  bei Kameraausfall kennt alle vier Plätze. Der **Ton** lässt sich von jeder der vier Kameras wählen.
- Neu: **Latenzbewusster SRTLA-Sender** (`srtla/`, Patch auf BELABOX/srtla Commit 37862da, AGPL-3.0). Er misst Laufzeit und
  Jitter jeder Leitung und sendet über die guten; langsamere Leitungen bleiben als Reserve. Das verhindert den Einbruch der
  Bitrate bei Leitungen mit unterschiedlicher Laufzeit (z. B. DSL + Mobilfunk). Wird von `install.sh` gebaut und nach
  `/usr/local/bin/srtla_send` installiert (das Original bleibt als Rückfall unter `/usr/bin`). Schlägt der Bau fehl, bleibt
  der Original-Sender. Einstellung **Verteilung auf die Sendewege**: "Beste Leitung bevorzugen" (Standard) oder "Alle
  Leitungen gleichzeitig nutzen".
- Neu: **WLAN-Karte** (Hotspot als weiterer Sendeweg): Netze suchen, verbinden, trennen, vergessen; Verbindungsstatus mit
  Netzname, IP und Signal; "Als Sendeweg nutzen" speichert sofort. Root-Helfer `pipbox-wifi.py` (feste Aktionen, strenge
  Prüfung, Passwort nur über stdin an nmcli, nie gespeichert). Das Kameranetz wird nie angefasst.
- Neu: **Upload-Anzeige** zeigt alle verbundenen Netzwerkkarten (Ethernet, WLAN, USB/Mobilfunk) und die Summe.
- Geändert: **Oberfläche neu geordnet.** Live steht oben, alle Karten sind zusammenklappbar (Zustand wird im Browser
  gemerkt, "Alle zu-/aufklappen" in der Kopfzeile). Status fasst Meldungen, CPU, Arbeitsspeicher und Upload zusammen,
  Kameras (RTMP + DJI) und "Senden und Netze" (WLAN + SRTLA) sind je eine Karte. Pipeline-Karte: Kamera, Ecke und Verzögerung
  stehen pro Bild untereinander. Sendewege als Zeilen mit Live-Upload.
- Behoben: Eine DJI-Kamera, die nur noch als Rest in der Bluetooth-Liste steht (z. B. Action 6 vor der Kopplung), wird nicht
  mehr als verbindbar behandelt; die Fehlermeldung nennt jetzt den Grund. Hinweis: Neue Kameras erst im Kopplungsmodus
  verbinden; ggf. Werkseinstellung, wenn die Kamera keine Kopplungsabfrage zeigt.
- Neu: Tests `tools/test_ui_backend.py` (vier Kameras, Tonauswahl, Verteilung, WLAN-Anfragen und -Prüfungen).

## 0.9.8 (Beta)
- Verbessert: **Kameras kommen nach einem Ausfall zuverlässiger zurück.** Die Kameras verbinden sich nach einem Ausfall nacheinander statt
  gleichzeitig (ein Bluetooth-Chip bricht parallele Verbindungsversuche gegenseitig ab: "le-connection-abort-by-local", "Dienste der Kamera
  nicht aufgelöst"). Höchstens eine neue Verbindung je Wächter-Durchlauf. Ist das Kameranetz (USB-Router) weg, wird nicht neu verbunden und
  keine Wartezeit verbraucht; kommt es wieder, werden die Wartezeiten zurückgesetzt und nach 8 s die Kameras neu verbunden.
- Neu: Tests `tools/test_daemon.py` (Wächter und Verbindungssperre, ohne Bluetooth).

## 0.9.7 (Beta)
- Neu: **Automatisches Umschalten bei Kameraausfall.** Fällt eine genutzte Kamera länger als 5 Sekunden aus, sendet die Box mit den
  übrigen weiter (fehlt das Hauptbild, wird das kleine Bild zum Hauptbild). Eine zurückgekehrte Kamera kommt erst nach 60 Sekunden
  stabilem Signal zurück, damit die Übertragung nicht pendelt. Läuft keine Kamera, wartet die Box ruhig, statt dauernd neu zu starten.
  Nur der Encoder startet beim Umschalten neu, die Verbindung zum Server bleibt. Die Verzögerung folgt der Kamera, nicht dem Platz.
  Schalter "Automatisch umschalten" in der Pipeline-Karte (Standard: an), Hinweis "Notbetrieb" in der Live-Karte.
  Gilt ab dem nächsten "Live gehen".
- Neu: Position **"unten Mitte"** für das kleine Bild. Sie erscheint in der Auswahl, sobald der Überlagerungs-Baustein neu gebaut
  ist (beim Software-Update automatisch); mit dem alten Baustein wird sie nicht angeboten.
- Neu: Tests in `tools/` (Umschaltlogik, Positionsfunktion).

## 0.9.6 (Beta)
- Behoben: Die Karte "System-Updates" zeigte nach einem Neustart der Box weiter gelb "Neustart…". Der Zustand "Neustart" wird jetzt
  als erledigt gewertet, sobald die Box seit dem Befehl neu gestartet ist (Boot-Kennung, sonst Zeitvergleich).

## 0.9.5 (Beta)
- Geändert: In der Kameraliste (RTMP) stehen Kameras ohne Signal unten, die sendenden oben.

## 0.9.4 (Beta)
- Neu: Die CPU-Karte zeigt die Lüfter-Ansteuerung in Prozent ("Lüfter: 24 % (Ansteuerung)"). Das ist der Sollwert (PWM), keine gemessene
  Drehzahl: die ROCK 5B Plus hat keinen Drehzahlanschluss am Lüfter. Ohne erkennbaren Lüfter bleibt die Zeile ausgeblendet.
- Geändert: In der Pipeline-Auswahl steht nur noch der Kameraname. Der Schlüssel in Klammern erscheint nur, wenn zwei Kameras denselben Namen haben.

## 0.9.3 (Beta)
- Behoben: Verbundene DJI-Kameras fehlten nach der Bluetooth-Suche in der Liste. Dadurch ließen sich ihre Einstellungen nicht
  ändern und sie nicht stoppen. Jetzt bleiben alle Kameras sichtbar, die laufen sollen, auch nach einem Neustart des Dienstes.
- Neu: Die Box merkt sich einmal gesehene DJI-Kameras. Ausgeschaltete stehen unten in der Liste, ihre Einstellungen bleiben änderbar;
  starten lassen sie sich erst, wenn sie nach dem Einschalten gefunden wurden.
- Geändert: Die Bildrate in der Kameraliste steht immer einfach als "30 fps", ohne den Zusatz "(eingestellt)".

## 0.9.2 (Beta)
- Protokolle begrenzt: Journal höchstens 30 MB und 7 Tage, Zustandsprotokoll höchstens 4 MB (etwa 1,5 Tage), Protokolle der Update-
  und Fernzugriff-Helfer je 512 KB. Das Journal wird dauerhaft gespeichert, damit nach einem Ausfall Spuren bleiben.

## 0.9.1 (Beta)
- Neu: **Fernzugriff über Tailscale** direkt aus der Oberfläche (freiwillig): installieren, mit dem eigenen Konto verbinden,
  die Oberfläche nur im privaten Netz freigeben, trennen, abmelden. Knöpfe zu den Stores für Android, iPhone, Mac, Windows, Linux.
  Anleitung: `ANLEITUNG-Fernzugriff.md`. Funnel (öffentlich) wird nie eingeschaltet, die Karte warnt, falls es aktiv ist.

## 0.9.0 (Beta)
- Erste Beta-Version. Aus der Alpha-Phase heraus: stabiler Betrieb mit drei Kameras in der Pipeline getestet.
- Neu: Software-Update aus der Oberfläche (Suchen, Installieren). Die letzten 5 Versionen bleiben gesichert; man kann gezielt
  auf eine Version wechseln, auch auf eine ältere (gesichert oder als Release-Marke vX.Y.Z auf GitHub).
- Neu: Verzögerung für Hauptbild und beide kleine Bilder per Regler, live ohne Neustart der Sendekette.
- Neu: Kamera umbenennen, Auswahl ohne doppelte Kameras oder Ecken, Ausgangswerte nach Rolle (Hauptbild 1080p/8 Mbit/s,
  kleine Bilder 720p/4 Mbit/s).
- Neu: Zustandsprotokoll für den Fall eines Totalausfalls, Hinweis in der Oberfläche, wenn das Kameranetz fehlt.
- Verbessert: Kamera-Dienst folgt der Adresse der Box im Kameranetz, sucht gemeinsam und heilt hängende Bluetooth-Adapter.

## 0.1.0 (Alpha)
- Weboberfläche, RTMP-Kameras, DJI-Kopplung per Bluetooth, SRTLA-Serverliste, Pipeline, eigene Sendekette.
