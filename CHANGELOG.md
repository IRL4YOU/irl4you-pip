# Änderungen

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
