# Änderungen

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
