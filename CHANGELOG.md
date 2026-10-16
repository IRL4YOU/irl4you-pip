# Änderungen

## 1.0.64
- Behoben: Liefert eine DJI-Kamera nur noch Ton und kaum Video (Action 5 Pro), baut der Dienst den Stream jetzt wirklich neu auf (Stopp, Vorbereiten, Start) statt ihn als „läuft noch“ weiterzuverwenden.

## 1.0.63
- Der Chat ist wieder wie vor 1.0.62 (Lesen, Schreiben, Moderation, Anmeldung, Ereignisse, Akku- und Systemwarnungen im Chat), aber sein Code steht in eigenen Dateien: `twitch.py`, `web/chat.js`, `web/chat.css`.
- Bleibt aus 1.0.62: NPU-Auslastung und Neuübertragungen entfernt, „x GiB frei“, minimierte Oberfläche.

## 1.0.62
- Der Twitch-Chat ist ausgebaut: Die Karte Chat bettet jetzt das Chat-Widget von Twitch ein (Kanalname über ⚙, nur im Browser gespeichert). Anmeldung, Moderation, Ereignisse, Akku-Warnung und Systemwarnungen im Chat entfallen; alte Zugangsdaten werden gelöscht.
- Entfernt: NPU-Auslastung und Neuübertragungen aus den Details. Arbeitsspeicher steht als „x GiB frei“.
- HTML-Dateien der Oberfläche minimiert, nicht benutzte Übersetzungen entfernt.

## 1.0.61
- Behoben: Bei plötzlichem Einbruch der Verbindung sank die Bitrate zu langsam, SRT verwarf Pakete (Bild-Artefakte, Tonaussetzer, Issue #1). Der Regler halbiert jetzt bei starkem Stau; im Test 60 % weniger verworfene Pakete. Der belacoder wird beim Update neu gebaut.
- Behoben: Eine DJI-Kamera, die nur noch Ton und kaum Video liefert (Action 5 Pro), wird nach 25 s neu verbunden.

## 1.0.60
- Die Adresse der öffentlichen Statistik ändert sich nie mehr von selbst: Der Schlüssel wird zusätzlich in `stats-token` gesichert. Ausschalten und erneutes Einschalten behält ihn; sind die Einstellungen verloren oder kaputt, wird er aus der Kopie geholt. Einen neuen Schlüssel gibt es nur über „Neuen Schlüssel erzeugen“.

## 1.0.59
- Bildrate von Handys schon vor dem Livegang: Verbundene Kameras ohne Bildrate in den Stream-Daten zeigen sie jetzt auch, wenn die Sendung noch nicht läuft. Die Box liest den Stream etwa 6 Sekunden vom eigenen nginx und zählt nur Bilder (kein Dekodieren, höchstens eine Messung gleichzeitig). Werte nahe 24, 25, 30, 50 oder 60 werden gerundet. Gilt für die Statusseite und die Statistik.

## 1.0.58
- Statistik: alles, was unter „Status“ steht, einschließlich der Details (Senden mit allen Zahlen, Upload und Download je Netz, alle Temperaturzonen, jeder Prozessorkern mit Takt, GPU, NPU, Speicherplatz). Jeder Block einzeln wählbar (neu: „Prozessor-Details“, „Speicherplatz“). Schon erzeugte Links und heruntergeladene Seiten laufen weiter (es kamen nur Felder dazu). Die Seite zeigt „Box offline“ mit „letzte Daten vor …“, wenn die Verbindung ausfällt, und verbindet sich von selbst neu.
- Bildrate von Kameras ohne Angabe (zum Beispiel Handys): Die Oberfläche und die Statistik zeigen die von belacoder gemessene Rate, wenn die Kamera keine meldet (bei laufender Sendung in der Betriebsart „Alle Kameras immer bereit“).

## 1.0.57
- Die Verzögerung der Kameras verschiebt jetzt wirklich Bild und Ton (bis 3000 ms, vorher ohne Wirkung, höchstens 750). Nötig: belacoder wird beim Update neu gebaut.

## 1.0.56
- Behoben: Bei starkem Bitratenabfall konnten Bild und Ton aussetzen. Der Regler reagiert jetzt früher (Meldung von Bittersweet, Issue #1).
- Behoben: Die DJI Osmo Action 6 riss etwa jede Minute ab und war nie als sendend erkannt. Die Kamera hängt dem Streamnamen ein Leerzeichen an, das wird jetzt abgefangen.

## 1.0.55
- Öffentliche Statistik für OBS und Homepage: Schalter „Statistik öffentlich“ in der Karte „Fernzugriff“ gibt nur die Statistik über Tailscale Funnel auf Port 8443 frei (die Oberfläche bleibt privat). Neue Seite `statistik.html` (WebSocket, jede Sekunde, Einstellungen in der Adresse), Zugriff nur mit geheimem Schlüssel, höchstens 3 Betrachter, im Schnappschuss nie IP/MAC, Stream-ID oder Serveradresse.
- „Freigabe beenden“ entfernt nur noch die eigene Freigabe (je Port statt `funnel reset`); die Selbstheilung prüft Oberfläche und Statistik getrennt.

## 1.0.54
- Gerundete Ecken gehen jetzt auch bei „Alle Kameras immer bereit“ (Compositor-Engine): neue Plugin-Elemente `pbtap` und `pbround` runden die kleinen Bilder und zeichnen den Rahmen. Ältere Plugins laufen wie bisher.
- Tailscale: Die öffentliche Freigabe wird nach einem Stromausfall selbst neu angewendet (nur wenn gewollt, Tailscale läuft und die Uhr stimmt); tailscaled startet nach Netz und Zeitsynchronisation, bei einem Uhrsprung nach seinem Start einmal neu. Das Protokollpaket zeigt Tailscale-Start, Uhr und Selbstheilung.

## 1.0.53
- Neuer, bereinigter Stand des Repositorys (Version 1.0.53).
- Behoben: Die Verzögerung der Kameras wirkte unter 450 ms nicht mehr. Jetzt bleiben die eingestellten Unterschiede erhalten.
- Eine mit „Ausschalten“ getrennte DJI-Kamera bleibt auch nach einem Neustart aus.
- Kameraliste am Handy: Anfasser zum Verschieben, Ausschalten und Entfernen in einer eigenen Zeile, Ziel-Linie beim Ziehen.
- Facecam: Der Ton bleibt bei der Facecam, auch wenn das Hauptbild wechselt.
- Ansichten für Desktop und Mobil mit eigenen Menüs, Reihenfolge und Design.
