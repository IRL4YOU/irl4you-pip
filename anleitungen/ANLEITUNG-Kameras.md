# Kameras an der IRL4YOU BOX anschließen

Diese Anleitung zeigt, wie du Kameras an die Box bringst: Handy-Apps und RTMP-Kameras, DJI-Kameras per Bluetooth und
einen HDMI-Eingang. Alles findest du in der Weboberfläche der Box (Port 8780) in der Karte **Kameras**.

Für **GoPro** gibt es eine eigene Anleitung: [ANLEITUNG-GoPro.md](ANLEITUNG-GoPro.md).

Was wir noch nicht mit einer echten Kamera geprüft haben, steht jeweils ehrlich dabei.

## 1. RTMP-Kameras und Handy-Apps (IRL Pro, Moblin)

Jede Kamera, die RTMP senden kann (zum Beispiel ein Handy), schickt ihr Bild an eine Adresse, die die Box dir anzeigt.

1. Öffne die Karte **Kameras** und dort **Neue RTMP-Kamera anlegen**.
2. Gib einen **Namen** ein (zum Beispiel "Hauptkamera"). Der Schlüssel darf leer bleiben, dann wird er automatisch vergeben.
3. Klicke **Kamera hinzufügen**. Die Kamera erscheint unter **Aktive Kameras** mit ihrer RTMP-Adresse.
4. Trage die Adresse in der Kamera-App ein. Oder, einfacher, nimm den **QR-Code**:
   - Wähle in der Zeile mit dem QR-Code die App (**IRL Pro** oder **Moblin**) und klicke **QR-Code erzeugen**.
   - Scanne den Code mit der Kamera-App des Handys und öffne den Link. Die App übernimmt die Verbindungsdaten.
   - Das Handy muss die Box erreichen können. Der Code enthält den Kamera-Schlüssel, zeig ihn also nicht herum.
5. Starte die Übertragung in der App. Die Kamera sollte nach kurzer Zeit mit grünem Punkt in der Liste stehen (siehe Abschnitt 4).

Gut zu wissen:

- Neue Streams erkennt die Box automatisch. Du kannst Kameras jederzeit umbenennen und entfernen.
- **Entfernen heißt entfernen:** Eine entfernte Kamera verschwindet überall (Kameraliste und, bei DJI, die DJI-Karte) und wird von einem noch sendenden Stream nicht von selbst wieder aufgenommen. Willst du sie zurück, füge sie von Hand hinzu oder richte die DJI-Kamera neu ein.
- Eine Handy-Kamera kann über eine eigene Verbindung der Box senden, zum Beispiel über einen zweiten Router. Die angezeigte
  Adresse gilt dann für diese Verbindung. Ohne eigene Wahl gilt die **Hauptverbindung**.
- Manche Scan-Apps erkennen den Link von IRL Pro nicht. Dann nimm die Kamera-App des Handys oder importiere den Link in IRL Pro.
- Mit Moblin und IRL Pro haben wir den QR-Code geprüft.

## 2. DJI-Kameras per Bluetooth

Die Box koppelt DJI-Kameras selbst, übergibt ihnen ein WLAN und das RTMP-Ziel und startet den Stream.
Die Oberfläche nennt diese Modelle: Osmo Action 3, 4, 5 Pro, 6, Osmo 360, Osmo Pocket 3 und 4.
Mit Action 4, Action 5 Pro und Action 6 haben wir gearbeitet. **Pocket 3: nur im Modus „Nur Akkustand lesen“ geprüft** (die Kamera sendet selbst per RTMP, die Box zeigt Akku und Ladesymbol); Stream-Start über die Box ist bei ihr und bei weiteren Modellen noch nicht mit echter Kamera geprüft.

### Suchen und koppeln

1. Stecke einen **Bluetooth-Stick** an die Box (siehe unten).
2. Schalte die Kamera ein, aktiviere Bluetooth und lege sie nahe an die Box. Eine neue oder zurückgesetzte Kamera muss im
   Kopplungsmodus sein.
3. Öffne **DJI-Kameras (Bluetooth)** und klicke **Nach DJI-Kameras suchen**.
4. Klicke bei deiner Kamera auf den Knopf zum Hinzufügen.
5. Bestätige die **Kopplungsabfrage an der Kamera** und halte die Kamera wach.
6. Die Karte der Kamera zeigt jeden Schritt (Sucht, Verbindet, Koppelt, Bereitet vor, WLAN wird übergeben, Stellt ein, Stream startet, Streamt).

Mit **Verbinden**, **Neu verbinden** und **Trennen** steuerst du die Kamera von Hand. Mit **Automatisch verbinden und streamen**
verbindet die Box von selbst, auch nach Ausfällen. Mehrere Kameras verbindet sie nacheinander, nicht gleichzeitig.

### Verbindung je Kamera wählen

Im Bereich **Verbindung** der Kamerakarte wählst du bei **Verbindung der Kamera**, worüber die Kamera zur Box funkt:

- **WLAN-Hotspot oder WLAN-Netz der Box:** Die Kamera bekommt Name und Passwort selbst.
- **Jede andere Verbindung** (Ethernet, USB-Router, Modem): Trage den WLAN-Namen und das Passwort ein, dem die Kamera beitreten
  soll. Unter **Netze in der Nähe** kannst du ein Netz auswählen, **Netze suchen** startet die Suche.
- **Manuell:** WLAN-Name, Passwort und Adresse der Box selbst eintragen.

Ist die Kamera schon verbunden, gelten Änderungen erst ab der nächsten Verbindung.

### Bild und Stream

Im Bereich **Bild und Stream** stellst du je Kamera ein: **Auflösung** (480p, 720p, 1080p), **Bildrate** (25 oder 30 fps; 60 gibt es
beim DJI-Livestream nicht), **Bitrate** (0,5 bis 16 Mbit/s) und **Stabilisierung** (Aus, RockSteady, RockSteady+,
HorizonBalancing, HorizonSteady). Welche Kamera Hauptbild oder kleines Bild ist, legst du in der Karte **Bildaufbau** fest.

### Bluetooth-Stick

Die eingebauten Bluetooth-Module der Boxen empfangen schlecht, ein USB-Stick ist besser. Unter **Verbindungen** im Abschnitt
**Bluetooth** siehst du, welche Sticks laufen, und eine Meldung, wenn ein Stick keinen Adapter ergibt.

| Stick | Stand |
|---|---|
| ASUS USB-BT500 | getestet, läuft an der Orange Pi 5 Plus |
| TP-Link UB500 | Box richtet den Treiber beim Einstecken selbst ein (dauert wenige Minuten); mit diesem Stick noch nicht an der Box geprüft |
| weitere Realtek-Sticks (zum Beispiel Mercusys MA530) | wie der UB500, nicht geprüft |
| UGREEN Bluetooth 5.4 und 6.0 | Box richtet den Treiber beim Einstecken selbst ein (dauert wenige Minuten); getestet mit dem UGREEN BT6.0 an der Orange Pi 5 Plus |

Der Treiber wird nie während einer Übertragung eingerichtet. Nach einem Wechsel des Sticks fragt die Kamera eventuell einmal
erneut nach der Kopplung.

### Handyverbindungen und Controller

*Beitrag von Bittersweet1987; auf der Entwicklerbox nicht selbst mit echten Geräten geprüft.* Beides steht unter **Verbindungen** im Abschnitt **Bluetooth**.

- **Handyverbindungen:** Koppeln: Die Box ist dafür zwei Minuten sichtbar, am Handy wählst du „belabox“. Danach liest die Box alle 10 Minuten kurz den **Akkustand des Handys** und trennt wieder (keine Dauerverbindung). Die Liste zeigt je Handy Akku, Zeit der letzten Abfrage und eine Meldung, wenn es nicht erreichbar ist; **Jetzt lesen** fragt sofort, **Entfernen** hebt die Kopplung auf. Android meldet den Akku in 20-%-Schritten, ein iPhone in Schritten von etwa 10 %.
- **Controller:** Bluetooth-Geräte mit Tasten (Gamepad, Mini-Tastenfeld, Fernauslöser). **Nach Controllern suchen** zeigt die Geräte in der Nähe, **Koppeln** koppelt, vertraut und verbindet. Gekoppelte Controller stehen mit Zustand und Akku (falls das Gerät ihn meldet) in der Liste; **Entfernen** hebt die Kopplung auf.
- **Menü „Controller-Tasten“:** Sobald ein Controller gekoppelt ist, erscheint ein eigenes Hauptmenü (zwischen „Bildaufbau“ und „Fernzugriff“). Du wählst je Taste eine Funktion: **Hauptbild**, **Kleines Bild 1, 2, 3**, **Aktuelle Tonquelle stumm schalten** oder **Zur nächsten Tonquelle wechseln**. Die Bild-Funktionen tauschen das Hauptbild wie der Knopf in der Oberfläche (ein zweiter Druck tauscht zurück). Bei Xbox-Controllern zählen die Trigger LT/RT und das Steuerkreuz wie Tasten.

## 3. HDMI-Eingang als Kamera "HDMI"

Der HDMI-Eingang der Box (zum Beispiel eine DJI Action 5 per USB-C-HDMI-Kabel) wird zu einer normalen Kamera namens "HDMI".

1. Verbinde die Kamera mit dem HDMI-Eingang der Box und schalte sie ein. Sie muss ein Bild ausgeben.
2. Öffne in der Karte **Kameras** den Abschnitt **HDMI- und USB-Kameras**. Unter **HDMI-Eingang** steht, ob ein Signal da ist.
3. Setze das Häkchen bei **Als Kamera senden**.
4. Unter **Bild und Ton** wählst du Bildrate (25 oder 30 fps), Bitrate und Ton (HDMI-Ton oder ohne Ton) und klickst **Speichern**.
5. In der Karte **Bildaufbau** wählst du die Kamera "HDMI" wie jede andere (Hauptbild, kleines Bild).

Der Dienst startet bei Signal und nach einem Ausfall von selbst.
**Noch nicht mit echter Kamera geprüft** sind der eingerichtete Dienst und die Bedienung über die Oberfläche; mit einer echten Action 5
lief bisher nur ein Probelauf. Verzögerung ist nicht gemessen. USB-Kameras als Quelle: siehe den nächsten Abschnitt.

### USB-Webcam als Quelle (neu)

Eine Kamera, die sich per USB als **Webcam (UVC)** meldet, kann die Box genauso als Kamera einspeisen, zum Beispiel eine **Action-Kamera im Webcam-Modus**.

1. Kamera per USB-C an die Box anschließen und **einschalten**. Bei DJI-Action-Kameras im USB-Modus **„Webcam“** wählen (an der Kamera).
2. Im Abschnitt **HDMI- und USB-Kameras** bei **Quelle** die **USB-Webcam** wählen. Darunter steht, welche Kamera die Box gefunden hat und welches Bildformat sie nimmt.
3. Das Häkchen **Als Kamera senden** setzen. Die Kamera erscheint als **„USB-Kamera“** in der Kameraliste und im Bildaufbau.

Die Box probiert die Bildformate selbst durch (**MJPEG 1080p**, MJPEG 720p, H.264 1080p, H.264 720p, dann Rohbild) und nimmt das erste, das ein Bild liefert. MJPEG kommt zuerst, weil die Box es mit der eingestellten **Bitrate** neu kodiert; der H.264-Strom einer Action-Kamera über USB hat nur etwa 1 Mbit/s (Action 5 Pro: 1,3 Mbit/s in 1080p). Läuft ein Format nicht stabil, nimmt die Box das nächste.

**Bildformat wählen:** Bei der USB-Webcam gibt es im Bereich **Bild und Ton** die Auswahl **Bildformat**: *Automatisch* (MJPEG zuerst, wie oben), *MJPEG* oder *H.264*. Bei *H.264* nimmt die Box zuerst den Strom der Kamera und reicht ihn durch; MJPEG bleibt als Rückfall, falls die Kamera kein H.264 liefert. Probiere beides, wenn du das Bild vergleichen willst: Die Action 5 Pro lieferte im dunklen Raum nur 1,3 Mbit/s in H.264 (bei MJPEG kodiert die Box mit der eingestellten Bitrate), bei Tageslicht und Bewegung kann der H.264-Strom der Kamera mehr liefern. Nach dem Wechsel startet die Einspeisung kurz neu. Bei der Action 6 schneidet sie in 1080p die 8 schwarzen Füllzeilen unten weg, die die Kamera anhängt; andere Kameras gehen unverändert durch. Den Ton nimmt sie vom USB-Mikrofon derselben Kamera, sonst sendet sie Stille. Es gibt **eine** Quelle für die Kamera „HDMI/USB“: Wer die USB-Webcam wählt, nutzt den HDMI-Eingang nicht gleichzeitig.

**Stand:** Mit der **Osmo Action 6** (Webcam-Modus, Hub mit eigenem Netzteil) kommt das Bild an: H.264 in 1080p30 direkt aus der Kamera.

**Wichtig – Strom:** Die Action 6 braucht im Webcam-Modus mehr Strom, als ein USB-Anschluss der Box oder ein Hub ohne Netzteil liefert. Dann meldet sie sich nur kurz als „DJI-Gerät“ (USB `2ca3:0025`), trennt sich nach ein paar Sekunden wieder und wird nie zur Webcam (`2ca3:8004`). An der Kamera steht dann dauernd „Webcam wird vorbereitet/verbunden“. Die Box erkennt das und meldet **„Die Kamera meldet sich nur kurz am USB und trennt sich wieder, meist fehlt ihr Strom. Bitte einen USB-Hub mit eigenem Netzteil verwenden.“** Abhilfe: Kamera über einen **USB-Hub mit eigenem Netzteil** anschließen, dann Kamera aus- und wieder einschalten und neu anstecken.

Wenn es sonst klemmt: Kamera ausschalten, USB abziehen, Webcam-Modus wählen, neu anstecken und „Als Kamera senden“ aus- und wieder einschalten. Meldung „Die Kamera liefert kein Bild“: Kamera nicht im Webcam-Modus oder schläft.

### Nur Akkustand lesen

Sendet eine DJI-Kamera per HDMI, kannst du sie zusätzlich per Bluetooth koppeln und in ihrer Karte **Nur Akkustand lesen
(Kamera sendet per HDMI)** einschalten. Dann liest die Box nur den Akkustand (für den Status und die Twitch-Warnung).
Mit einer echten Action 5 geprüft; **mit der Action 6 noch nicht geprüft**.

## 4. Kamera-Ampel und Hinweise bei Ausfall

Der Punkt vor jeder Kamera (in der Kameraliste und in der Karte **Status**) zeigt den Zustand. Fahr mit der Maus darüber für eine Erklärung.

| Farbe | Bedeutung |
|---|---|
| Grün | Die Kamera sendet und ist im Bild. |
| Gelb | Sie sendet, ist aber noch nicht oder nicht mehr im Bild, etwa beim Wiederverbinden. Eine zurückgekehrte Kamera wird erst nach 60 Sekunden stabilem Signal wieder aufgenommen. |
| Rot | Kein Signal. |
| Grau | Status unbekannt. |

Fällt etwas aus, erscheint im Status eine Meldung mit der Uhrzeit. Ein Klick auf das **i** dahinter zeigt, was du tun kannst. **Hast du eine Kamera oder ein Handy bewusst abgeschaltet oder abgezogen, ist das kein Fehler: Mit dem × an der Meldung (bei Kamera, HDMI und USB) schließt du sie.** Ohne ×-Klick verschwindet sie, sobald die Kamera wieder sendet (die Meldung „Kamera ausgefallen“ spätestens nach 6 Stunden, die USB-Meldung 5 Minuten nach „wieder da“ oder nach 24 Stunden). Ein späterer neuer Ausfall meldet sich wieder:

| Meldung | Was sie heißt, was du tust |
|---|---|
| DJI-Kamera ausgefallen (mit Name) | Bluetooth getrennt, Akku leer oder Kamera ausgeschaltet. Kamera aufwecken und die Verbindung prüfen. |
| Kamera ausgefallen (mit Name) | Die Kamera sendet nicht mehr. Akku, WLAN-Verbindung und Kabel prüfen. |
| HDMI: kein Signal | Kabel prüfen und die Kamera einschalten. |
| USB-Gerät getrennt (zum Beispiel USB-WLAN-Adapter, USB-Bluetooth-Adapter) | Gerät getrennt, abgezogen oder ausgefallen. Kabel und Stromversorgung prüfen. Die Meldung nennt den **Namen des Geräts** (aus dem Kernel-Protokoll); hinter dem **i** stehen Anschluss und USB-Kennung. Kommt das Gerät von selbst wieder, steht „wieder da“ dabei und die Meldung verschwindet nach 5 Minuten ohne neue Trennung. Sonst schließt du sie mit dem **×**, spätestens nach 24 Stunden ist sie weg. In den **ersten 2 Minuten nach dem Start der Box** zählen Trennungen nicht (manche Sticks melden sich beim Hochfahren mehrmals neu an), und Meldungen aus der Zeit vor einem Neustart erscheinen danach nicht mehr. |
| ... mit "Bitte Stromversorgung prüfen" | Der USB-Anschluss wurde abgeschaltet (Störung oder Spannungseinbruch). Netzteil und USB-Hub prüfen, den Adapter möglichst direkt am Board anstecken. |

Fällt eine Kamera aus, schaltet die Box auf die übrigen um. Das dauert etwa 5 Sekunden ohne Bild.

## 5. Hinweise zum Kamera-WLAN

DJI-Kameras setzen im WLAN gelegentlich für einige Sekunden aus. In unserem Aufbau (GL.iNet-Router mit Mobilfunk, vier Kameras,
Orange Pi 5 Plus) hat geholfen:

- **Nur 5 GHz, WPA2, 20 MHz Kanalbreite.** Nach der Umstellung von WPA3 auf WPA2 gab es über Stunden keinen Aussetzer mehr.
  WPA2 mit AES und einem langen Passwort ist für ein reines Kameranetz sicher genug. Ein Kanalwechsel allein brachte nichts.
- **Kanal 36 bis 48 (kein DFS).** Die Osmo Action 5 Pro unterstützt laut Datenblatt nur 5150 bis 5250 und 5725 bis 5850 MHz.
  Welche Kanäle bei dir erlaubt sind, prüfe bitte selbst.
- **Sendewege nicht im Kamerafunk.** Eine Beobachtung (nicht bewiesen): Lag der Hotspot eines Handys als Sendeweg auf demselben
  5-GHz-Kanal wie das Kamera-WLAN, stiegen Laufzeitspitzen. Besser sind Sendewege per Kabel oder auf einem anderen Band.
- Die Action 5 Pro und die Action 6 fallen öfter aus als die beiden Action 4 (Ursache offen).
- Die Box wartet bei RTMP-Kameras 15 statt 4 Sekunden, bevor sie eine stumme Kamera aus dem Bild nimmt. Das beseitigt die
  Aussetzer nicht, verhindert aber, dass dabei der Encoder neu startet.

## 6. Wenn etwas nicht geht

| Problem | Das hilft |
|---|---|
| Handy-App verbindet nicht | Erreicht das Handy die Box? Adresse oder QR-Code neu erzeugen und in der App prüfen. Findet die Scan-App den Link nicht, nimm die Kamera-App des Handys. |
| Punkt bleibt rot | Die Kamera sendet nicht. Läuft die Übertragung in der App oder an der Kamera? Akku und Verbindung prüfen. |
| Punkt bleibt gelb | Normal beim Wiederverbinden: bis zu 60 Sekunden warten. |
| "Kein Bluetooth-Adapter gefunden" | Einen USB-Bluetooth-Stick einstecken (Tabelle in Abschnitt 2). |
| Stick wird als nicht nutzbar gemeldet | Treiber fehlt oder Stick wird nicht unterstützt. Der Hinweis in der Karte nennt den Grund. |
| DJI-Kamera wird nicht gefunden | Kamera einschalten, Bluetooth aktivieren, nahe an die Box legen, im Kopplungsmodus, dann erneut suchen. |
| DJI-Kamera koppelt nicht | Kopplungsabfrage an der Kamera bestätigen und die Kamera wach halten. |
| Kamera hängt beim Verbinden | In ihrer Karte **Trennen**, kurz warten, dann **Verbinden** oder **Neu verbinden**. |
| Kamera tritt dem WLAN nicht bei | Verbindung der Kamera prüfen: Name und Passwort stimmen? Bei fremdem Netz 5 GHz und WPA2 nutzen (Abschnitt 5). |
| Wiederholte Aussetzer im Kamera-WLAN | WPA2, 5 GHz, Kanal 36 bis 48, 20 MHz (Abschnitt 5). |
| "HDMI: kein Signal" | Kabel prüfen, Kamera einschalten, sie muss ein Bild ausgeben. |
| HDMI-Kamera fehlt im Bildaufbau | Ist **Als Kamera senden** eingeschaltet? Steht unter **HDMI-Eingang** ein Signal? |
| "Bitte Stromversorgung prüfen" | Netzteil und USB-Hub prüfen, Adapter direkt am Board anstecken. |
| Nichts davon hilft | In der Karte **Protokolle** die Protokolle herunterladen (Passwörter und Adressen werden ersetzt, trotzdem kurz durchsehen) und für ein GitHub-Issue verwenden. |
