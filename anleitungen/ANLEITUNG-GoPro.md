# GoPro an der IRL4YOU BOX

Eine GoPro sendet als **ganz normale RTMP-Kamera** zur Box. Die Box steuert sie nicht (kein Bluetooth, kein Start von der Box aus, kein Akkustand). Du richtest die GoPro einmal ein und startest den Livestream an der Kamera oder in der GoPro-App.

**Stand (9. Oktober 2026):** Mit einer **Hero 8** und der **Labs-Firmware** funktionieren die **QR-Codes** (Weg B): Die Kamera sendet über den Hotspot der Box mit etwa 3 Mbit/s und läuft im Dauerbetrieb (vom Entwickler bestätigt, auf der Box nachgesehen). Zuvor war schon der Weg über die **GoPro-App** (Weg A) mit der Hero 8 nachgewiesen (1080p, H.264, mehrere Minuten stabil). Andere Modelle (Hero 9 bis 13) sind nicht geprüft. Die GoPro ist die Kamera, bei der es am ehesten klemmt; die Tabelle unten sammelt, woran es meist liegt.

## Was du brauchst

- Eine GoPro **Hero 8 bis 13**. Für die QR-Codes muss die **GoPro-Labs-Firmware** auf der Kamera sein (Firmware-Änderung an der Kamera, die du selbst machst; Anleitung bei GoPro Labs). Für den Weg über die GoPro-App brauchst du sie nicht.
- Einen **WLAN-Stick an der Box mit eingeschaltetem Hotspot** (siehe unten). Die GoPro verbindet sich mit diesem WLAN der Box; die Codes der Oberfläche sind auf den Hotspot der Box abgestimmt.
- Die Box mit der Oberfläche (Port 8780) im selben Netz wie dein Handy.
- **Wichtig (Hinweis vom Entwickler, 9. Oktober 2026):** Die GoPro darf **nicht frisch zurückgesetzt** sein. Sie muss **einmal vorher mit der GoPro-App verbunden** worden sein, und das **WLAN der Box** (der Hotspot) muss in der Kamera **einmal eingerichtet** sein (Schritt 2). Danach läuft es nach seinen bisherigen Erfahrungen sehr gut.

## Schritt 1: Hotspot der Box einschalten

1. In der Oberfläche die Karte **Verbindungen** öffnen und beim WLAN-Stick **Hotspot-Modus** einschalten. Über **Einstellen** wählst du Name, Passwort, 2,4 oder 5 GHz und Kanal.
2. Warten, bis der Hotspot läuft. Die GoPro findet das Netz unter dem gewählten Namen.

Tipp: Bei den DJI-Kameras war ein WLAN mit WPA2 und 5 GHz deutlich stabiler als 2,4 GHz; probiere es bei der GoPro ebenso, wenn sie 5 GHz kann (noch nicht mit der GoPro gemessen).

## Schritt 2: GoPro mit dem Hotspot verbinden

Das muss **vor** allem anderen geschehen: In der **GoPro-App** (Quik) die Kamera **einmal** mit der App verbinden und dabei das WLAN der Box (den Hotspot) in der Kamera einrichten. Ohne diesen Schritt kann die GoPro die Box nicht erreichen. Nach einem **Zurücksetzen der Kamera** muss dieser Schritt wiederholt werden.

## Schritt 3: Die Kamera in der Box anlegen

In der Karte **Kameras** unter **Kamera hinzufügen** einen Namen eingeben. Die Box zeigt danach die **RTMP-Adresse** der Kamera. Sie sieht so aus:

`rtmp://<Adresse der Box>:1935/publish/<Schlüssel>`

Wenn die GoPro über den Hotspot der Box verbunden ist, ist die Adresse der Box im Hotspot-Netz zu nehmen (zum Beispiel `10.42.0.1`). Die QR-Codes unten tragen sie automatisch ein.

## Weg A: Über die GoPro-App (mit Hero 8 geprüft)

1. GoPro-App öffnen, Kamera verbunden lassen (Schritt 2).
2. In der App den **Livestream** wählen und als Ziel eine **eigene RTMP-Adresse** angeben (die Namen der Menüs ändern sich mit der App-Version).
3. Die RTMP-Adresse aus Schritt 3 eintragen, Auflösung wählen (1080p oder 720p) und starten.
4. In der Oberfläche leuchtet die Kamera-Ampel nach kurzer Zeit **grün** oder **gelb** (gelb: sendet, ist aber noch nicht im Bild).

## Weg B: Über die QR-Codes (Labs-Firmware, mit Hero 8 im Dauerbetrieb)

1. Beim Eintrag der Kamera (bei **Kamera hinzufügen** und bei jeder RTMP-Kamera) in der kleinen Zeile **GoPro (HERO 8–11)** oder **GoPro (HERO 12/13)** wählen und **QR-Code erzeugen** drücken. Das „?“ daneben erklärt es kurz.
2. Es erscheinen **drei große Codes**, die du nacheinander vor die GoPro hältst (die Kamera liest QR-Codes nur zuverlässig, wenn sie groß genug sind; am Handy füllen sie deshalb die Breite):
   1. **WLAN** – merkt sich Name und Passwort des Box-Hotspots. Der Code erscheint nur, wenn auf der Box ein Hotspot läuft. Sonst steht dort ein Hinweis: am WLAN-Stick den Hotspot einschalten.
   2. **RTMP** – merkt sich die Adresse der Kamera (mit der Hotspot-Adresse der Box).
   3. **Start** – startet den Livestream in 1080p.
3. Danach sollte die GoPro zur Box senden. Prüfe in der Oberfläche, ob die Kamera auftaucht und die Ampel grün wird.

Hero 12 und 13 nutzen für „Start“ nur den Anhang `!GL`, die anderen Modelle mit dem Vorsatz für 1080p. Bei Hero 12/13 verlangt die GoPro-Labs-Seite, einmal **Auto-Upload** an der Kamera einzuschalten.

**Wichtig:** Die Codes enthalten den Kamera-Schlüssel. Nicht abfotografieren und weitergeben.

## Wenn etwas nicht geht

| Problem | Ursache und Lösung |
|---|---|
| Kamera erscheint nicht / bleibt **rot** | Die GoPro hat die Box nicht erreicht. War sie **zurückgesetzt**? Dann einmal wieder mit der GoPro-App verbinden und das WLAN der Box einrichten (Schritt 2). Sonst prüfen, ob sie mit dem **Hotspot der Box** verbunden ist (in der GoPro-App) und ob die RTMP-Adresse die **Hotspot-Adresse** enthält, nicht die Adresse deines Heimnetzes. |
| QR-Code wird nicht gelesen | Code **größer und ruhig** halten, Abstand etwas ändern, Display heller stellen, Handy ruhig auflegen. Reihenfolge einhalten: erst 1, dann 2, dann 3. Zum Test funktioniert Weg A auch ohne die Codes. |
| „Ohne Hotspot gibt es keinen WLAN-Code“ | Am WLAN-Stick unter **Verbindungen** den **Hotspot** einschalten, dann den Code neu erzeugen. |
| Die Labs-Codes bewirken nichts | Die Kamera braucht die **Labs-Firmware**, und die Befehle müssen zum Modell passen (Hero 8–11 oder 12/13). Erst der Hotspot, dann die GoPro damit verbinden, dann die Codes 1, 2, 3. Weg A über die GoPro-App funktioniert auch ohne Labs. |
| Sendet kurz, bricht dann ab | WLAN zu schwach: GoPro näher an den Stick, 5 GHz probieren, Kanal ohne Störungen wählen. Der Hotspot muss laufen und darf nicht ausgeschaltet werden. |
| Bild im Bildaufbau fehlt | Die Kamera muss **grün** sein und im Bildaufbau gewählt werden; gelb heißt: sendet, ist aber noch nicht im Bild. Nach einem Ausfall wird eine Kamera erst nach 60 Sekunden stabilem Signal wieder aufgenommen. |

## Fehlersuche mit dem Protokoll

In der Karte **Protokolle** den Knopf **Protokolle herunterladen** drücken. Zwei Abschnitte helfen bei der GoPro:

- **Verbindungen am RTMP-Eingang**: zeigt jede Verbindung, auch eine, die kein Bild liefert (Adresse, Programm der Gegenstelle, sendet oder schaut, Dauer, abgeworfene Bilder). Taucht die GoPro dort gar nicht auf, hat sie die Box nicht erreicht.
- **Hotspot der Box**: zeigt, ob der Hotspot läuft, welche Geräte angemeldet sind (Empfang, Dauer) und die vergebenen Adressen.

Passwörter, Schlüssel, Adressen und MAC-Adressen werden vorher durch Platzhalter ersetzt; sieh die Datei trotzdem kurz durch, bevor du sie weitergibst. Wenn es bei dir nicht klappt, öffne bitte ein **GitHub-Issue** oder schreibe im Discord und hänge das Protokoll an. Rückmeldungen zu weiteren Modellen helfen sehr.

## Was nicht geht

- **Kein Akkustand und keine Steuerung von der Box aus.** Die Box spricht mit der GoPro nicht per Bluetooth (das gibt es nur für DJI-Kameras). Start und Stop machst du an der Kamera oder in der GoPro-App.
- Die Labs-Codes sind **nur mit einer Hero 8 geprüft**. Hero 9 bis 13 sollten nach der Labs-Dokumentation gehen, sind hier aber nicht getestet; Rückmeldungen helfen.
