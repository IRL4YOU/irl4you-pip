# Änderungen

## 0.9.143 (Beta)
- **Übersetzungen geprüft (alle 14 Sprachen, in der laufenden Oberfläche gemessen):** Alle rund 1800 Texte sind in jeder Sprache vorhanden. Gefunden und behoben:
  - **Kopfzeilen der Chat-Ereignisse** („🚀 Raid · 42 Zuschauer“, „💎 Cheer“, „🎁 Geschenk-Abos“, „💜 Follow“, „🎯 Kanalpunkte“) blieben in allen Sprachen deutsch, weil das Symbol und der Text als ein Textstück übersetzt wurden. Jetzt wird nur der Text übersetzt.
  - Es fehlten die Texte „Ton“ (HDMI-Karte), „(optional)“, „App“ (QR-Code erzeugen), „(Version …)“ (Sicherung) und „(eingestellt …)“ (Bitrate), dazu „(Demo)“ in sechs Sprachen. Die Prüfung `tools/i18n_extract.py` übersah einzelne kleingeschriebene Wörter mit eingefügtem Wert; diese stehen jetzt in `tools/i18n_extra_keys.json`.
- Test: `tools/test_eventsub.py` war gelegentlich rot, weil die beiden Abonnements parallel angelegt werden und die Reihenfolge im Protokoll zufällig ist; der Test sortiert jetzt nach Typ.

## 0.9.142 (Beta)
- **Neu: Live-Ereignisse des Kanals über EventSub (nach der Twitch-Anleitung):** **Follows** (💜 „Follow“) und **Kanalpunkte-Einlösungen** (🎯 „Kanalpunkte · 500“, mit Belohnung und Text des Zuschauers) erscheinen als farbige Karten im Chat. Das war bisher nicht möglich (Follows gibt es im Chat nicht). Subs, Geschenk-Abos, Raids und Cheers kommen weiter aus dem Chat, der sie vollständiger liefert (EventSub meldet zum Beispiel Resubs ohne Text nicht); doppelt wären sie nur Lärm.
  - **Einschalten:** hinter dem ⚙ „Ereignisse einschalten“ (einmal bei Twitch bestätigen, neue Rechte `moderator:read:followers` und `channel:read:redemptions`); danach steht dort „Ereignisse an“. Wer die Moderation schon hat, behält sie. Nur für den **eigenen** Kanal.
  - **Technik:** eigener kleiner WebSocket-Client ohne Zusatzbibliothek (RFC 6455: Aufbau mit Prüfung der Antwort, Ping/Pong, Fragmente, Größenbegrenzung, Zeitgrenze auch bei Steuerrahmen; Verstöße beenden die Verbindung). Die Verbindung läuft über die Sendewege wie der Chat. Die Abonnements werden in einem eigenen Faden angelegt (Twitch schließt eine Sitzung, die nicht binnen 10 s abonniert): beide gleichzeitig, mit kurzer Zeitgrenze (4 s), zuerst über den Weg, auf dem die Verbindung steht, mit einmal geholtem Zugangsschlüssel; was scheitert, wird nach 5 und 20 Sekunden noch einmal versucht, „gibt es schon“ (409) zählt als angelegt, eine späte Antwort einer alten Sitzung überschreibt nie die neue. Entdoppeln über `message_id`, Neuverbinden bei Stille (1,5 × Keepalive, auch ein Ping der Gegenstelle zählt als Lebenszeichen), `session_reconnect` wie von Twitch beschrieben: erst die neue Verbindung aufbauen und auf ihr `session_welcome` warten, dann die alte schließen (nur zu Twitch-Adressen, mit und ohne Pfad, ohne neues Abonnieren). Entzieht Twitch alle Abonnements oder ändern sich die Rechte, beginnt eine neue Sitzung. Die Verbindung steht nur, solange die Oberfläche den Chat abfragt. Die Statusabfrage der Oberfläche wartet nie hinter einer Anfrage an Twitch. Der Zugangsschlüssel steht nie in einer Antwort oder einem Protokoll.
- **Chat senden über die Twitch-Schnittstelle (Helix `chat/messages`)** statt über IRC, sobald der Zugang das Recht `user:write:chat` hat (alle neuen Anmeldungen; wer sich nicht neu anmeldet, sendet weiter über IRC). Twitch antwortet auch mit „gesendet“, wenn die Nachricht verworfen wurde (Spam- oder Tempofilter, nur Abonnenten, langsamer Modus): jetzt zählt nur `is_sent`, sonst steht der Grund von Twitch in der Meldung („Twitch hat die Nachricht nicht gesendet: …“). Die Anfrage wird nie ein zweites Mal über einen anderen Weg gesendet; ging sie raus und die Antwort ging verloren, steht „Verbindung gestört, unklar ob es angekommen ist“ statt eines falschen „nicht verbunden“.
- **Anmeldung:** Die Grundrechte sind jetzt `chat:read chat:edit user:write:chat`. Moderation und Ereignisse sind getrennt zuschaltbar; was der Zugang schon hat, bleibt bei einer neuen Anmeldung erhalten.
- Tests: neue Datei `tools/test_eventsub.py` (30 Tests gegen Gegenstellen auf 127.0.0.1: Rahmen und Verstöße, Aufbau, Ereignisse, Doppelte, Neuverbinden, Stille, abgelehnte und wiederholte Abonnements, Entzug, Rechtewechsel, Ruhen, Senden mit Rückmeldung, einmaliges Senden ohne Wiederholung). Eine unabhängige Prüfung des neuen Codes hat neun Punkte ergeben, alle behoben und mit Tests abgesichert. Neue Texte in allen 14 Sprachen (maschinell).

## 0.9.141 (Beta)
- **Kopfleiste am Handy während der Sendung zeigt jetzt die vorhandene Pulsanzeige und die Kamerapunkte aus dem Chat** (dieselben Elemente, sie ziehen vom Chat in den Kopf und danach wieder zurück; Farbe und Verlauf wie dort). Die in 0.9.139 eigens gebaute EKG-Linie samt Untermenü „Pulsanzeige“ (Grün/Gelb/Rot-Grenzen unter SRTLA) entfällt. Der Pfeil ganz rechts klappt wie bisher die Knöpfe auf (dann sind Pulsanzeige und Punkte im Kopf weg, er sieht aus wie ohne sie).
- Die Karte „SRTLA“ behält die Untermenüs „SRTLA-Server“, „Leitungssteuerung“ (Verteilung der Netze) und „Bitrate und Latenz“.
- Die Chat-Gestaltung aus 0.9.131 bis 0.9.134 (Kopfzeile einzeilig, Zahnrad oben, Amplitude, Ereigniskarten, Moderation) wurde geprüft: nichts davon wurde überschrieben. Entfallen ist nur der Anordnen-Modus (ersetzt durch „Optionen“ in 0.9.135).

## 0.9.140 (Beta)
- **Neu: kurze Hinweise bei Ausfällen, jeweils mit einem „i“ dahinter** (Issues #43 und #44: Der USB-WLAN-Adapter war der einzige Sendeweg und fiel aus, „disabled by hub (EMI?)“). Unter „Status“ steht eine Zeile mit Art und Uhrzeit (Ortszeit des Geräts, gestern: „gestern um 23:04“); ein Tipp auf das „i“ zeigt ein bis zwei Sätze, was man tun kann. Das „i“ gehört zu den Hilfe-Knöpfen und verschwindet mit ihnen (Optionen > Hilfe-Knöpfe).
  - **„USB-WLAN-Adapter getrennt · um 23:04“** (auch Bluetooth-Adapter, Router, Kamera, sonstiges Gerät, je nach Produktname). Wurde der Anschluss durch Störung oder Spannungseinbruch abgeschaltet („EMI?“) oder meldet der Kernel Überstrom, steht dahinter **„Bitte Stromversorgung prüfen“**. Quelle ist das Kernel-Protokoll (ohne Rechte lesbar); die Ereignisse werden gemerkt (24 Stunden, höchstens zwei Meldungen) und überstehen einen Neustart des Dienstes. Fällt die ganze Box aus, sieht der Kernel das nicht (dafür gibt es den Abschnitt „Vorheriger Start“ in den Protokollen).
  - **„DJI-Kamera ausgefallen“ / „Kamera ausgefallen“ mit Name und Uhrzeit:** eine Kamera, die seit dem Start des Dienstes gesendet hat und seit mehr als 20 Sekunden nicht mehr sendet (sechs Stunden lang; die Meldung verschwindet, sobald sie wieder sendet).
  - **„HDMI: kein Signal“:** der HDMI-Eingang meldet einen Fehler oder (eingeschaltet) kein Signal seit mehr als 20 Sekunden.
  - **Unterspannung** kann diese Platine nicht messen (kein Fühler für die Eingangsspannung); der indirekte Hinweis ist „Bitte Stromversorgung prüfen“ bei einem abgeschalteten USB-Anschluss.
- **Protokolle: neuer Abschnitt „USB-Ereignisse“** mit den Kernelzeilen der letzten 3 Tage (Trennung, abgeschalteter Anschluss, Überstrom, Fehler beim Erkennen) und den gemerkten Ereignissen.
- Tests: `tools/test_usb.py` (9 Tests; am echten Kernel-Protokoll einer Box ohne Ausfälle keine falschen Meldungen). Neue Texte in allen 14 Sprachen (maschinell).

## 0.9.139 (Beta)
- **Neu: Designs** (Optionen > Design). Das bisherige Aussehen bleibt als „Standard“ und ist die Vorgabe; dazu zwei neue, auf Lesbarkeit und Bedienung am Handy wie am Rechner ausgelegt, jeweils hell und dunkel (der Knopf in der Kopfleiste gilt weiter):
  - **Klar:** ruhig und großzügig, größere Schrift, Überschriften in normaler Schreibweise, weiche Karten, runde Knöpfe (mindestens 44 Pixel hoch).
  - **Kompakt:** dichte Ansicht mit wenig Abstand und eckigen Formen, viele Werte auf einen Blick; am breiten Bildschirm stehen die kleinen Karten (Fernzugriff, Updates, Protokolle …) in zwei Spalten.
  - Alle Knöpfe sind in beiden Designs deutlich zu erkennen: Rand mindestens 3:1, Schrift mindestens 4,5:1, auch bei „ausgewählt“, „nicht verbunden“, „deaktiviert“ und in der Fußleiste am Handy. `tools/test_designs.py` rechnet die Farbkontraste nach.
  - Gemerkt wird die Auswahl im Browser (wie Hell/Dunkel); auch die Anmeldeseite und die Sprachauswahl folgen dem Design. Ohne Auswahl ändert sich nichts.
- **Menüeinstellung gilt jetzt für alle Geräte:** Reihenfolge der Hauptmenüs und ausgeblendete Menüpunkte (Optionen) liegen auf der Box (`ui-layout.json`) statt nur im Browser; was am Rechner ausgeblendet ist, ist es auch am Handy. Geändert wird es sofort auf der Box, andere Geräte übernehmen es beim Öffnen der Seite oder beim Zurückkehren zu ihr. Hat die Box noch nichts gespeichert, gibt das erste Gerät seinen bisherigen Stand ab. Das Design bleibt je Gerät wählbar.
- **Streammodus, Design und Reihenfolge ausblendbar:** In Optionen > Menüpunkte anzeigen gibt es jetzt auch den Knopf „Streammodus“ in der Kopfleiste sowie die Unterpunkte „Design“ und „Reihenfolge der Hauptmenüs“. „Optionen“ und „Menüpunkte anzeigen“ bleiben immer sichtbar.
- **Kamera-Knöpfe in der Fußleiste (Handy), neue Bedienung:** kurz drücken = zum Hauptbild machen, Doppelklick = kleines Bild ausblenden oder wieder einblenden (Ton bleibt), lang drücken = Kamera deaktivieren (nicht im Stream, Ton nicht nutzbar), nochmal lang = aktivieren. Vorher: kurz = ausblenden, doppelt = deaktivieren, lang = Hauptbild. Die Hilfetexte (ⓘ und die Hinweise an den Knöpfen) sind angepasst. Eine deaktivierte Kamera wird mit jeder Geste wieder aktiviert (das Bild bleibt ausgeblendet). Achtung: Läuft die Sendung, kann das Wechseln des Hauptbilds den Encoder neu starten (siehe „Hauptbild tauschen ohne Unterbrechung“).
- **Hilfe-Knöpfe „i“ ausblendbar:** In Optionen > Menüpunkte anzeigen gibt es die Zeile „Hilfe-Knöpfe (i)“. Ist sie abgewählt, sind alle „i“ samt ihren Hilfetexten weg.
- **Einstellungen sichern/einspielen enthält jetzt mehr:** die Optionen (Reihenfolge und Ausblenden der Menüs, auch „Streammodus“, „Design“ und „Hilfe-Knöpfe“), den HDMI-Eingang (Ein/Aus, Bitrate, Bildrate, Ton), die Akku-Warnung im Twitch-Chat (Kanal, Bot-Name, Schwelle, Nachricht, nur bei Sendung; **ohne Token und ohne Twitch-Anmeldung**, die bleiben auf der Box; eingeschaltet wird sie nur, wenn hier ein Token oder eine Anmeldung vorhanden ist) und das Netzwerk, über das die Kameras die Box erreichen. Die Kameraliste samt Rolle und Verbindung je Kamera war schon dabei. Ältere Sicherungen lassen sich weiter einspielen; die Einstellungen werden wie bisher vorher gesichert und lassen sich zurückholen. Das Design (Standard, Klar, Kompakt) gehört zum Browser und ist nicht dabei.
- **Neu: Pulsanzeige in der Kopfleiste (Handy, während gesendet wird).** Der Kopf zeigt eine laufende EKG-Linie mit der Gesamtbitrate aller Sendewege: **grün** ab 6000 kbit/s, **gelb** ab 1000 kbit/s, darunter **rot** (je schlechter, desto schneller der Schlag; ein Wechsel der Stufe geht fließend ineinander über, Tempo und Farbe gleiten). Die Zahl hat feste Breite, die Linie bleibt bei ein-, zwei- und dreistelligen Mbit/s an ihrem Platz. Der Pfeil ganz rechts klappt die Knöpfe auf: aufgeklappt ist die Pulsanzeige weg und der Kopf sieht aus wie ohne sie; zugeklappt steht die Pulsanzeige da. Mit jedem Start und Ende der Sendung ist der Kopf wieder zugeklappt bzw. ganz da.
- **Die Karte „SRTLA“ hat Untermenüs:** „SRTLA-Server“, „Leitungssteuerung“ (darin „Verteilung der Netze“), „Bitrate und Latenz“ und neu **„Pulsanzeige“**: dort legt man selbst fest, ab wie vielen kbit/s Grün und Gelb gelten (Rot gilt immer ab 0 bis zum Wert für Gelb und ist als gesperrtes Feld zu sehen; Reihenfolge: Rot, Gelb, Grün). Jedes Untermenü hat seinen eigenen Speichern-Knopf; die Pulsanzeige gilt sofort, ohne Neustart der Sendung. Die Grenzen sind in der Einstellungssicherung enthalten.
- **Sicherung: Namen:** Die selbst vergebenen Namen der Verbindungen (Netzwerke) waren schon dabei und heißen jetzt auch so („Namen (Verbindungen, WLAN- und Bluetooth-Sticks)“). Neu: Ein in der Kameraliste umbenannter DJI-Kamera-Name geht beim Einspielen nicht mehr verloren (der DJI-Dienst gab der Kamera sonst seinen eigenen Namen zurück); es gilt der Name der Kameraliste.
- **Fußleiste am Handy, Fehler behoben:**
  - Ist nur eine Kamera verbunden, wechselt kurzes Drücken nicht mehr zu einer nicht verbundenen Kamera (Meldung statt Wechsel). Das prüft auch der Server (`/api/pipeline/swap`), am Rechner ist der Knopf einer nicht verbundenen Kamera gesperrt.
  - Mit nur einer Kamera bleibt der Ton-Knopf in der Fußleiste (lang drücken = stumm oder wieder laut); „Live“/„Stop“ ist allein in der Leiste nicht mehr über die ganze Breite gezogen.
  - Die Bedienhilfe (i) nennt jetzt auch Ton und Stumm, mit kurzen Zeilen: jede Zeile bleibt in jedem Design und in jeder Sprache in einer Zeile (die Schrift schrumpft bei Bedarf).
- Neue Texte in allen 14 Sprachen (maschinell übersetzt, nicht von Muttersprachlern geprüft); `tools/test_designs.py` und `tools/test_ui_layout.py` prüfen Auswahl, Speichern und die Stilregeln.

## 0.9.138 (Beta)
- **Übersetzungen:** Die 6 Texte des Abschnitts „Vorheriger Start“ (0.9.136) fehlten noch in allen Sprachen (Englisch: `test_english_is_complete` war rot). Jetzt in allen 14 Sprachen (maschinell übersetzt, nicht von Muttersprachlern geprüft). Alle Sprachdateien geprüft: nichts fehlt, alle Platzhalter stimmen.

## 0.9.137 (Beta)
- **Reihenfolge der Hauptmenüs jetzt unter „Optionen“** (Unterpunkt „Reihenfolge der Hauptmenüs“, ein- und ausklappbar): je Menü eine Zeile mit ▲ ▼, ausgeblendete Menüs stehen grau darin. Die ▲ ▼ in den Titelzeilen der Hauptmenüs entfallen, am Handy wie am Rechner. Gespeicherte Auswahl und Reihenfolge bleiben gültig.
- „Reihenfolge zurücksetzen“ steht jetzt in diesem Unterpunkt; „Alle einblenden“ bei „Menüpunkte anzeigen“. Neue Texte in allen 14 Sprachen (maschinell übersetzt).

## 0.9.136 (Beta)
- **Protokolle: neuer Abschnitt „Vorheriger Start“.** Er zeigt die Starts im Journal (mit Endzeit) sowie die letzten Kernel- und Dienstmeldungen vor dem letzten Neustart. So sieht man nach einem Hänger der Box, was zuletzt gemeldet wurde und wann sie stehen blieb. Gleiche Zugangsdaten-Schwärzung wie im übrigen Protokoll. Keine neuen Texte.

## 0.9.135 (Beta)
- **Neu: Menü „Optionen“** (zwischen Fernzugriff und Software-Update, ersetzt die Karte „Anpassen“ ganz unten). Darin „Menüpunkte anzeigen“ (ein- und ausklappbar): Haken weg = Menü oder Bereich ausgeblendet; „Alle einblenden“ und „Reihenfolge der Hauptmenüs zurücksetzen“. Ausgeblendet wird nur die Anzeige, an den Einstellungen ändert sich nichts.
- **Hauptmenüs verschieben:** jedes Hauptmenü hat oben rechts dauerhaft ▲ ▼ (wie bei den aktiven Kameras); ausgeblendete Menüs werden dabei übersprungen. Der Knopf „Anordnen“ in der Kopfzeile und der Anordnen-Modus entfallen. Die gemerkte Auswahl und Reihenfolge (je Browser) bleiben gültig.
- Auch „Details“ im Status lässt sich einzeln ausblenden. Neue Texte in allen 14 Sprachen (maschinell übersetzt, nicht von Muttersprachlern geprüft).

## 0.9.134 (Beta)
- **Neu: Moderation lässt sich ausschalten.** Hinter dem ⚙ in der Chat-Karte steht jetzt „Moderation ausschalten“, wenn sie an ist. Dann verschwinden sofort alle ⋯ und die Box nutzt die Rechte nicht mehr (auch die Befehle `/ban`, `/timeout` und `/unban` im Eingabefeld sind gesperrt). „Moderation einschalten“ schaltet sie ohne neue Anmeldung bei Twitch wieder ein; nur wenn der Zugang die Rechte nicht hat, ist wie bisher einmal die Bestätigung bei Twitch nötig. Der Schalter bleibt nach einem Neustart und nach der Erneuerung des Zugangs erhalten. **Wichtig:** Twitch lässt nicht zu, die Rechte eines Zugangs nachträglich zu verkleinern; sie fallen erst weg, wenn man sich abmeldet und neu ohne Moderation anmeldet.
- **Amplitude am Rechner höher** (28 statt 20 Pixel, Zahlen 8 Pixel): Die „0“ war unten abgeschnitten, jetzt sind alle drei Zahlen ganz sichtbar. Die Regeln für schmale Handys (Skala aus, schmalerer Verlauf) wurden von einer Grundregel überschrieben und greifen jetzt wirklich.
- Keine neue Anmeldung nötig; neue Texte in allen 14 Sprachen.

## 0.9.133 (Beta)
- **Behoben: Die Zahlen links vom Verlauf der Amplitude waren am Rechner zu groß, die „0“ ragte unten aus dem Verlauf heraus.** Die Schrift ist kleiner (7 statt 9 Pixel), alle drei Zahlen stehen jetzt innerhalb der Höhe des Verlaufs.
- **Chat, hinter dem ⚙: „Angemeldet als … · Moderation an“.** Man sieht jetzt, dass die Moderation schon eingeschaltet ist (der Knopf „Moderation einschalten“ verschwindet dann). Die Knöpfe ⋯ erscheinen an den Nachrichten **anderer** Zuschauer; an den eigenen Nachrichten des Streamers gibt es keine, weil Twitch das Löschen eigener Streamer-Nachrichten nicht zulässt.

## 0.9.132 (Beta)
- **Behoben: In der Kopfzeile der Chat-Karte rutschte das ⚙ am Handy in eine zweite Zeile** (die Prüfung für 0.9.131 hatte das Umbrechen erlaubt). Die Kopfzeile bleibt jetzt einzeilig, das ⚙ steht immer rechts oben; bei Breiten bis 460 Pixel entfällt die kleine Skala links vom Verlauf, der Verlauf ist etwas schmaler. Geprüft bei 320, 360, 390, 430 und 500 Pixel, auch mit vier Kamerapunkten, ohne seitliches Scrollen. Nur beim Anordnen der Karten darf die Zeile umbrechen.
- Keine neuen Texte.

## 0.9.131 (Beta)
- **Neu im Chat: Emotes von 7TV, BetterTTV und FrankerFaceZ** erscheinen als Bild (global und je Kanal; die Box lädt die Listen selbst über die Sendewege und hält sie 30 Minuten). Die Bildadressen baut die Box nur aus geprüften Kennungen, die Oberfläche lädt nur von diesen drei Hosts; ohne Verbindung oder bei einem Fehler bleibt das Wort als Text stehen.
- **Neu: „Alle löschen“** im Moderationsmenü (⋯): räumt alle Nachrichten einer Person ab (ein Timeout von 1 Sekunde, ohne Strafe). „Löschen“ entfernt wie bisher genau diese eine Nachricht.
- **Gegenlese-Prüfung des Chats (drei unabhängige Prüfungen, Befunde einzeln nachgestellt und behoben):**
  - **Anmeldung:** Der neue Erneuerungsschlüssel wird zuerst gespeichert (mit fsync), erst danach das Konto geprüft; ein Absturz oder Stromausfall dazwischen kostet die Anmeldung nicht mehr. Die Erneuerung wird nie ein zweites Mal über einen anderen Weg gesendet, wenn die Antwort verloren ging. Bei Netz- oder Twitch-Fehlern gibt es eine wachsende Wartezeit; ein 401 löst einmal eine Erneuerung aus. Eine neue Anmeldung („Moderation einschalten“) sperrt ein angemeldetes Konto nicht mehr aus, wenn sie scheitert. Ein einzelner 429- oder 5xx-Fehler beendet die Anmeldung per Code nicht mehr. Es wird nie still als Bot-Konto geschrieben, wenn der Zugang gerade erneuert wird. Die Oberfläche wartet nie hinter einer Anfrage an Twitch.
  - **Chat lesen:** Kanalwechsel (auch A→B→A) lässt nie zwei Lesefäden laufen; die Wartezeit nach einem Abbruch beginnt nach einer langen Sitzung wieder bei 3 Sekunden; ein Weg, bei dem TLS oder die Begrüßung hängt, wird gemieden; ein Zuschauer mit dem Namen „RECONNECT“ kann die Verbindung nicht mehr trennen; eine unlesbare Zeile beendet die Sitzung nicht mehr; Emote-Stellen stimmen auch bei doppelten Leerzeichen; ein Namensauflösungsfehler sperrt keinen Sendeweg mehr; die normale Verbindung kommt vor gesperrten Wegen.
  - **Senden:** Emoji-Folgen (Familie, Flaggen) und geschützte Leerzeichen sind erlaubt, Steuer- und Richtungszeichen nicht; unsinnige Zahlen und Nicht-Objekte als Anfrage führen zu einer Fehlermeldung statt zu einem Absturz.
  - **Oberfläche:** Kein doppelter Verlauf mehr nach einer Fehlerantwort; beim Wechsel des Kanals verschwinden die alten Zeilen; Zeitgrenze (8 s) für die Abrufe, bei ausbleibenden Messwerten wird die Amplitude grau; Emotes und Namen auch mit Emoji im Text richtig; Chat-Texte werden nie übersetzt (sonst wurde aus „Hilfe“ „Help“); die Moderationsknöpfe sind größer und auch an schon vorhandenen Zeilen da, wenn die Moderation später eingeschaltet wird; das Menü rutscht nicht mehr unter dem Finger weg; „Bildschirm anlassen“ hinterlässt keine verwaiste Sperre mehr; der Ton bei Ereignissen funktioniert auch auf dem iPhone (Vibration gibt es dort nicht); Eingabefeld ohne Zoom auf iOS; dunkle Namensfarben bleiben lesbar; die Kopfzeile der Chat-Karte bricht auf schmalen Handys um statt abzuschneiden.
- Echt geprüft (nur lesend, anonym): 80 Sekunden Chat eines großen Kanals (495 Zeilen, 319 Emotes von 7TV/BTTV/FFZ, keine Fehler) und 150 Sekunden eines ruhigen Kanals (eine Sitzung ohne Abbruch, die Nachfrage bei Stille hält sie offen). Anmeldung, Moderation und Senden nur gegen nachgebaute Gegenstellen (`tools/test_chat.py` 29 Tests, `tools/test_twitch_login.py` 17).
- Übersetzungen: die neuen Texte in allen 14 Sprachen (maschinell).

## 0.9.130 (Beta)
- **Handyansicht: weniger Platz oben verschenkt.** Rand über der Kopfleiste 4 statt 12 Pixel, Abstand zum Inhalt 6 statt 16, Abstand zwischen den Karten 10 statt 14; bei 375 Pixel Breite beginnt die Chat-Karte rund 28 Pixel weiter oben. Auf Geräten mit Notch bleibt der Abstand zur Notch erhalten.
- **Handyansicht: der Hinweisblock unter der Kopfleiste (zum Beispiel „Kein SRTLA-Server ausgewählt“ oder „Kamera … sendet gerade nicht“) erscheint nur noch, wenn es etwas zu melden gibt.** Sonst verschwindet er samt Abstand (weitere 25 Pixel Platz). Braucht einen Browser mit `:has` (iOS Safari ab 15.4, Chrome ab 105, Firefox ab 121); ältere zeigen nur den leeren Abstand.
- **Chat: jede zweite Nachricht minimal anders hinterlegt**, damit man die Nachrichten besser unterscheidet. Die Ereigniskarten (Sub, Raid, Cheer …) behalten ihre eigene Farbe.
- Keine neuen Texte, keine neuen Übersetzungen.

## 0.9.129 (Beta)
- **Behoben: Die Amplitude im Chat und die „Bitrate“ in Status → Details zeigten zu hohe, zappelnde Werte.** Sie nahmen die Senderate aus dem Sender (`send_mbps`); die lag auf einer Box mit 12 Mbit/s eingestellter Bitrate bei 15,6 bis 17,6, während die Zähler der Netzwerkkarten rund 13,6 Mbit/s zeigten. Beide Anzeigen nehmen jetzt die Summe der Zähler der gewählten Sendewege, dieselbe Zahl wie „Mbit/s ↑“ neben „Live“. Die Güte-Farbe der Amplitude nutzt ebenfalls diesen Wert.
- Die Skala der Amplitude geht jetzt von 0 bis 5, 10, 15, 20, 30 oder 50 Mbit/s (Mitte = Hälfte); rechts vom Verlauf steht die aktuelle Zahl.
- Keine neuen Texte, keine neuen Übersetzungen.

## 0.9.128 (Beta)
- **Chat: besondere Ereignisse fallen auf.** Sub, Resub, Geschenk-Abos (eine Sammelaktion als eine Karte), Raid, Cheer (Bits) und Ankündigungen erscheinen als farbige Karte mit Symbol und Menge; neue leuchten kurz auf. Hast du hochgescrollt, erscheint „Neues Ereignis ↓“. Optional (⚙, „Signal bei Ereignissen“, standardmäßig aus): zwei kurze Töne und Vibration. Follows gibt es im Twitch-Chat nicht (dafür bräuchte es eine andere Schnittstelle), sie fehlen deshalb.
- **Chat: Kopfzeile wie die Streamstatus-Anzeige für OBS.** Links ein pulsierender Punkt und ein kleiner Linienverlauf der Senderate der letzten 30 Sekunden mit Raster und Skala (Mbit/s). Die Farbe zeigt die Güte der Verbindung: grün gut, gelb mäßig, rot schlecht, grau keine Sendung (aus Sendewegen, Laufzeit, Sendepuffer und Senderate). Rechts bis zu vier kleine Punkte, einer je Kamera (grün sendet und ist im Bild, gelb noch nicht im Bild, rot kein Signal, grau unbekannt). Der Kanalname in der Kopfzeile entfällt.
- **Chat: nach der Anmeldung verschwindet die Konto-Zeile.** Ganz rechts bleibt ein kleines ⚙; dahinter stehen „Angemeldet als …“, „Moderation einschalten“, „Abmelden“ und die Ansicht.
- **Chat über die Sendewege: ein toter Weg fällt jetzt nach etwa 100 Sekunden auf** (vorher erst nach mehr als 5 Minuten): Die Box fragt bei Stille selbst bei Twitch nach (PING). Bleibt die Antwort aus, geht der Chat über den nächsten Sendeweg. Eine einzelne Chat-Verbindung wird dabei nicht auf mehrere Wege verteilt, sie weicht nur aus.
- Tests: `tools/test_chat.py` (19 Tests: Ereignisse, Nachfrage bei Stille). Die Amplitude und die Kamerapunkte sind in der Vorschau mit eingespielten Messreihen geprüft, noch nicht auf einer Box mit laufender Sendung.
- Übersetzungen: die neuen Texte in allen 14 Sprachen (maschinell).

## 0.9.127 (Beta)
- **Neu: Bereich „Chat“ in der Oberfläche** (Twitch-Chat des eigenen Kanals, am Handy gleich unter dem Live-Überblick).
  - **Lesen** ohne Token (anonym wie ein Zuschauer), mit Uhrzeit, farbigen Namen, Abzeichen (Streamer, Mod, VIP, Sub), Emotes als Bild und dünnen Trennlinien. Die Ansicht lässt sich über ⚙ anpassen (Uhrzeit, Abzeichen, Emotes, Linien, Schriftgröße).
  - **Anmeldung bei Twitch per Code** (Gerätecode wie am Fernseher, auch als QR): kein Token mehr einfügen. Die Anmeldung wird im Hintergrund erneuert; nach 30 Tagen ohne Nutzung muss man sich neu anmelden. **Schreiben** im Chat ist danach möglich.
  - **Moderation** (Knopf „Moderation einschalten“, dann erneute Bestätigung bei Twitch): ⋯ an jeder Nachricht mit Löschen, Timeout (10 Minuten, 1 Stunde) und Bannen, jeweils mit „Rückgängig“ statt Rückfrage; auch `/ban`, `/timeout`, `/unban` im Eingabefeld.
  - **Der Chat-Verkehr nutzt die Sendewege der Box:** erst der beste Weg, bei Fehler oder Stille der nächste, zuletzt die normale Verbindung. (Eine einzelne Verbindung wird nicht gebündelt.)
  - **Bildschirm anlassen** (Schalter 💡 in der Kopfzeile; braucht https, zum Beispiel über Tailscale).
  - **Akku-Warnung im Chat** (DJI Action 4/5/6) läuft jetzt auch über das Twitch-Konto, ohne eigenen Token.
- **Neu: Karten anordnen und ausblenden.** „Anordnen“ in der Kopfzeile (Handy: ⠿) zeigt ▲ ▼ an jeder Karte; ganz unten die Karte „Anpassen“ blendet Karten und Bereiche (DJI-Kameras, HDMI, Akku-Warnung usw.) aus. Gemerkt wird im Browser.
- **Ehrlicher Stand:** Anmeldung, Schreiben und Moderation sind gegen eine nachgebaute Twitch-Gegenstelle getestet (`tools/test_chat.py`, `tools/test_twitch_login.py`), noch nicht gegen das echte Twitch; das Ausweichen über mehrere Sendewege und das Anlassen des Bildschirms am Handy sind auf einer Box noch ungeprüft.
- Übersetzungen: alle neuen Texte in 14 Sprachen, dazu die 86 bisher fehlenden Texte aus 0.9.119 bis 0.9.124 (`tools/test_i18n.py` ist wieder grün). Maschinell übersetzt, nicht von Muttersprachlern geprüft.

## 0.9.126 (Beta)
- **GoPro: Voraussetzung im Hilfetext (hinter dem „?“ beim QR-Code) ergänzt.** Der Hotspot der Box muss zuerst eingerichtet sein und die GoPro muss vorher damit verbunden sein (in der GoPro-App); erst dann kommen die Codes 1, 2, 3 (Labs-Firmware). So funktionierte es beim Test mit einer Hero 8. Der Text steht in allen 14 Sprachen. Sonst keine Änderung.

## 0.9.125 (Beta)
- **GoPro-QR-Codes größer und besser lesbar.** Die Codes für die GoPro (Labs-Firmware) sind jetzt 340 Pixel groß und stehen untereinander (die GoPro-Seite selbst nutzt 360 Pixel); am Handy füllen sie die Breite. Die kleinen Codes (150 Pixel) las die Kamera vermutlich nicht zuverlässig. Der RTMP-Code (Code 2) nimmt jetzt die Adresse des Box-Hotspots (zum Beispiel `10.42.0.1`), wenn die GoPro darüber verbunden wird, und ohne laufenden Hotspot steht ein kurzer Hinweis statt des WLAN-Codes.
  - **Ehrlicher Stand:** Mit der **GoPro-App** (Live, eigene RTMP-Adresse, Hotspot der Box als Netz) sendet eine Hero 8 nachweislich zur Box (1080p, H.264, etwa 2,6 Mbit/s, mehrere Minuten stabil). Die **Labs-QR-Codes** haben bei diesem Test nicht ausgereicht; ob die größeren Codes es beheben, ist offen. Die Befehle entsprechen den Befehlen der offiziellen GoPro-Labs-Seite. Wenn es bei jemandem nicht klappt: bitte das Protokoll aus der Oberfläche anhängen (siehe unten). Bleibt die GoPro-Anbindung unzuverlässig, wird sie wieder ausgebaut.
- **Protokolle für die Fehlersuche bei RTMP-Kameras und dem Hotspot (neue Abschnitte).** „Verbindungen am RTMP-Eingang“ zeigt jede Verbindung am nginx, auch eine, die kein Bild liefert (Adresse, Programm der Gegenstelle, sendet oder schaut, Dauer, abgeworfene Bilder). „Hotspot der Box“ zeigt den Zustand des Zugangspunkts, die angemeldeten Geräte (Empfang, Dauer), die vergebenen Adressen und die Zeilen zu An- und Abmeldungen aus dem Journal, ohne Passwort. Alles geht wie bisher durch den Bereiniger (Adressen, Namen, Schlüssel). Beides läuft nur, wenn der Protokoll-Download ausgelöst wird.
  - Tests: `tools/test_logs.py` (2 neue Tests mit Beispielausgaben, auch dass Schlüssel und MAC-Adressen verschwinden). Auf der echten Box ausprobiert (nur lesend): die GoPro erscheint mit Adresse, Empfang und Dauer.
- Übersetzungen: die neuen Texte (Hinweis ohne Hotspot, neue Protokoll-Abschnitte) in allen 14 Sprachen. **Offen aus den Versionen davor:** 86 Texte aus 0.9.119 bis 0.9.124 haben in den zwölf Sprachen außer Englisch noch keine Übersetzung (`tools/test_i18n.py` zeigt es: unter 98 Prozent).

## 0.9.124 (Beta)
- **Behoben: Ein Bild, das auf der Box vergrößert wird (zum Beispiel 720p von Litchi oder DJI Fly als Hauptbild oder bei 90–100 % Größe), stoppte den ganzen Ausgang** (Issue #35). Ursache, gemessen: Der Hardware-Dekoder legt seine Bilder in Speicher ohne CPU-Zwischenspeicher ab; der Skalierer liest beim Vergrößern jedes Quellpixel mehrfach daraus. Auf einer RK3588 dauerte 720p auf 1080p **43 ms je Bild** (mehr als die 33 ms bei 30 Bildern/s), nach einer einmaligen Kopie in normalen Speicher **3,5 ms**. Auf der Box des Anwenders (4 GB) stand der Zweig bei 100 % eines Kerns, das Bild kam etwa 1 s zu spät, danach erzeugte der Compositor keine neuen Bilder mehr.
  - Neu: fünfter belacoder-Patch `belacoder/belacoder-frame-copy.patch` (belacoder `-sb12`). Er kopiert ein Bild, das größer werden soll, einmal in normalen Speicher, bevor es skaliert wird. Gleich große oder verkleinerte Bilder (die 1080p-Kameras als kleine Bilder, das 1080p-Hauptbild) laufen unverändert weiter. `SB_FRAME_COPY=0` schaltet es ab. Ohne Skalierung ändert sich nichts.
  - Gemessen auf einer Orange Pi 5 Plus (8 GB) mit einem 720p-Hauptbild und künstlicher Speicherlast (um die knappere Box nachzustellen): vorher 97 % eines Kerns, 15 Bilder/s und 1,1 s Rückstand, **nachher 9 %, 30 Bilder/s, −110 ms**. Die drei echten 1080p-Kameras: unverändert (Zweige bei 1–6 %).
  - **Beim Update baut die Box den belacoder neu** (der Installer erkennt den neuen Patch); das braucht Internet und einige Minuten. Auf der Box des Anwenders mit Litchi noch nicht geprüft.

## 0.9.123 (Beta)
- **Neu: Die Protokolle zeigen, was der Dekoder aus einer Quelle macht** (Issue #35: bei einer Quelle läuft der Zweig nach dem Dekoder auf einem Kern bei 100 %, bei den anderen nicht).
  - **Dekoder-Ausgang je Quelle:** Die Box dekodiert von jedem sendenden H.264-Eingang 4 Sekunden probeweise mit dem Hardware-Dekoder und schreibt auf, was hineingeht und herauskommt (Format, Größe, Bildrate, Seitenverhältnis, Farbinformationen, Speicherart).
  - **Stream-Prüfung der Quellen** nennt zusätzlich die kodierte Größe und den Beschnitt (zum Beispiel 1920x1088 mit 8 Zeilen Beschnitt unten) und die Darstellungsangaben im Stream (VUI: Bildrate, Seitenverhältnis, Farbbereich).
  - Tests: `tools/test_logs.py` (3 neue Tests); auf einer Box (0.9.122) mit drei Quellen einmal laufen lassen und die Ausgabe angesehen. Die Probe-Dekodierung belastet die Box für diese 4 Sekunden zusätzlich (je Quelle ein zweiter Dekoder). Englische Texte in `web/i18n/en.json`.

## 0.9.122 (Beta)
- **Neu: Die Protokolle zeigen jetzt, was die Box auslastet und was die Sendekette tut** (zur Fehlersuche, zum Beispiel Issue #35). Neue Abschnitte:
  - **Auslastung je Kern und je Dienst (Momentaufnahme über 2 s):** Last jedes Kerns in %, die Dienste nach Last (100 % = ein ganzer Kern), und die Threads mit der größten Last samt Kern, auf dem sie zuletzt liefen.
  - **System: Druck, freier Platz, Speicherbedarf:** Druck auf CPU, Speicher und Datenträger (PSI), freier Platz, die Prozesse mit dem größten Speicherbedarf.
  - **Netzwerk-Zähler (seit dem Start):** Pakete, Fehler und verworfene Pakete je Netzwerkkarte, dazu die Verbindungsübersicht.
  - **Ereignisse der Sendekette (belacoder):** wann ein Kamerazweig gestartet wurde und mit welchem Zeitversatz er an die Uhr der Sendekette angelegt wurde („Aligned … offset … ms“). Der Sender legt diese festen Meldungen (ohne Adressen und Schlüssel) in `/run/pipbox-send/belacoder-events.txt` ab.
  - **nginx: letzte Fehler** (zum Beispiel „stream not found“) und **Kernel (Video, Speicher, Temperatur, Abstürze, USB-Fehler)**: nur Fehler- und Warnzeilen zu Videodekoder, Speicher, Temperatur und USB, nicht das normale Startprotokoll.
  - **Zustandsprotokoll (alle 10 s):** jede Zeile nennt jetzt die Last je Kern seit der vorigen Zeile (`cores=25/11/10/…`, in %) und die Threads mit der größten Last (`hot=sbf3_lq:src:99%@5`, Name, Last, Kern). So sieht man auch hinterher, welcher Thread zu welchem Zeitpunkt einen Kern voll gemacht hat.
  - Tests: `tools/test_logs.py` (4 neue Tests mit einem nachgebauten `/proc`), `tools/test_ui_backend.py` (Zustandsprotokoll),, `tools/test_send_hardening.py` (Ereigniszeilen). Auf einer Box (0.9.121) einmal laufen lassen und die Ausgaben angesehen. Die englischen Texte stehen in `web/i18n/en.json`, die anderen Sprachen folgen mit der nächsten Übersetzungsrunde.

## 0.9.121 (Beta)
- **Neu geordnet: Hauptmenü und Karte „Kameras“.** Reihenfolge nach „Status“: **Verbindungen**, **SRTLA: Server, Bitrate und Latenz**, **Kameras**, dann „Bildaufbau: Kameras, Bild-in-Bild, Positionen“ und der Rest wie bisher.
  - Die Karte „Kameras“ hat Untermenüs, standardmäßig zugeklappt: **Aktive Kameras** (vorher „RTMP-Kameras“, mit Liste und Hauptverbindung), **Neue RTMP-Kamera anlegen** (Name, Schlüssel, „Kamera hinzufügen“, QR-Code), **DJI-Kameras (Bluetooth)** und **HDMI- und USB-Kameras**. Ganz am Ende steht als eigenes Untermenü **Akku-Warnung im Twitch-Chat (nur bei DJI)** (vorher im DJI-Menü).
  - Die Karte „Verbindungen“ ebenfalls: **Verbindungen zum Senden (Upload)**, **WLAN-Verbindungen (z. B. Handy-Hotspot, auch zum Senden nutzbar)**, **Bluetooth (für DJI-Kameras)** und **Zugriff auf diese Oberfläche**.
  - **Neu: Hinweis bei ungespeicherten Änderungen.** Statt „Alles im grünen Bereich“ steht rot „Noch nicht gespeicherte Änderungen in „Bild-in-Bild“ Anpassungen“; ein Klick springt zum Knopf „Bildaufbau speichern“ (am Handy steht der Hinweis bei den Meldungen). Gemeldete Warnungen bleiben dahinter stehen. „Akku-Warnung im Twitch-Chat“ heißt jetzt „Akku-Warnung im Twitch-Chat (nur bei DJI)“.
  - **Hinweistexte** hinter dem runden „i“ jetzt auch am Rechner (vorher nur am Handy); die beiden „?“-Knöpfe (QR-Codes, Automatik beim Start) sehen genauso aus.
  - Englische Texte für die neuen Beschriftungen und für die Texte der erweiterten Protokolle (0.9.120), die bisher fehlten (Test `test_english_is_complete`). Die anderen Sprachen folgen mit der nächsten Übersetzungsrunde.
  - Nur die Seite (`web/index.html`) und die Texte; keine Änderung an der Technik. Aussehen nicht von uns im Browser an einer laufenden Box geprüft, nur die Struktur (Reihenfolge, Untermenüs, Zustand zu).

## 0.9.120 (Beta)
- **Neu: Die Protokolle (Download in der Oberfläche) enthalten jetzt, was zum Verstehen von Bildfehlern gebraucht wird** (Anlass: Issue #35, DJI Mini 2 erzeugt ein Standbild; im Protokoll fehlten die Angaben der Quelle und die Kennzahlen der Sendekette). Vier neue Abschnitte:
  - **Kameras am Eingang (nginx-Statistik):** je Stream Auflösung, Bildrate, Codec, Profil, Stufe, Datenrate, Zuschauer und Laufzeit (die Schlüssel werden wie überall ersetzt).
  - **Bildaufbau der Sendekette (belacoder):** die Kennzahlen der Engine (Bilder je Sekunde und Rückstand je Zweig, Encoder, Compositor) und ihre Warnungen.
  - **Stream-Prüfung der Quellen:** liest für 5 Sekunden in jeden laufenden Eingang hinein und beschreibt ihn: Codec, Profil, Stufe, Auflösung, Bezugsbilder, Bildzeiten (Rate, Schrittweite, Rückwärtssprünge), Schlüsselbilder, B-Bilder (Zeitversatz), Tonspur (Codec, Rate, Kanäle) und die ersten Zeitstempel. Nur Kopfdaten und Zeiten, keine Bildinhalte. Anlass: Dieselbe Drohne streamt mit DJI Fly richtig und mit Litchi nicht; so sieht man, was Litchi anders sendet. Braucht `gst-launch-1.0` (rtmpsrc), sonst steht nur ein Hinweis da.
  - **Auslastung je Thread (Momentaufnahme):** zeigt, welcher Zweig oder Dienst einen Kern voll macht (Threads von GStreamer tragen den Namen des Elements).
  - Tests: `tools/test_logs.py` (4 neue Tests, mit einer Beispielstatistik von nginx). Am echten nginx und auf einer Box mit laufender Sendung nicht von uns geprüft: das Format der Statistik ist aus dem bestehenden Code der Oberfläche übernommen.

## 0.9.119 (Beta)
- **Neu: Verbindungen (Netzwerkschnittstellen) lassen sich benennen.** In den Einstellungen (Sendewege) steht neben jeder Verbindung ein Stift (✎); damit vergibst du einen eigenen Namen, zum Beispiel für „eth1“ den Namen „Router Keller“. Der Name ergänzt die Bezeichnung, er ersetzt sie nicht: Im Status (Up- und Download, Laufzeit je Weg) steht „eth1 (Router Keller)“, bei den Sendewegen „LAN (Router Keller)“, in der Verbindungswahl der Kameras ebenfalls. Leer lassen entfernt den Namen.
  - Gespeichert in `device-names.json` (Schlüssel `net:<Schnittstelle>`, getrennt von den Namen der WLAN- und Bluetooth-Sticks), also auch in der Sicherung der Einstellungen. Name: höchstens 40 Zeichen, keine Sonderzeichen. Technik: `DeviceNames.conn_names`, `POST /api/devname` (wie bei den Sticks), `conn_names` in `/api/metrics` und `/api/srtla`.
  - Tests: `tools/test_ui_backend.py` (`test_connection_names_add_to_the_interface_and_stay_apart_from_device_names`). Englische Texte in `web/i18n/en.json`, die anderen Sprachen folgen mit der nächsten Übersetzungsrunde. Noch nicht in der Oberfläche gesehen (nur der Server-Test und die Textprüfung liefen).

## 0.9.118 (Beta)
- **Neu: QR-Code für Kamera-Apps.** Unter „Kamera hinzufügen“ (und bei jeder RTMP-Kamera, die keine DJI- oder HDMI-Kamera ist) gibt es eine kleine Zeile: App wählen (IRL Pro, Moblin, GoPro HERO 8–11 oder HERO 12/13) und „QR-Code erzeugen“. Der Code erscheint erst nach dem Klick, ein zweiter Klick blendet ihn aus; die Erklärung steckt hinter einem kleinen „?“.
  - Ohne Namenseingabe: Unter „Kamera hinzufügen“ legt die Box bei Bedarf selbst eine neue RTMP-Kamera an („<App> 1“, automatischer Schlüssel) und zeigt ihren Code; für dieselbe App nimmt sie beim nächsten Mal die vorhandene.
  - Inhalt: IRL Pro `larix://set/v1?conn[][url]=…&conn[][name]=…` (Larix-Grove-Format von Softvelum), Moblin `moblin://?<URL-codierter JSON>` mit Codec H.264, GoPro (Labs-Firmware) drei Codes: WLAN merken (nur wenn auf der Box ein Hotspot läuft), RTMP-Adresse merken, Livestream starten (1080p). Der Code enthält den Kamera-Schlüssel.
  - Der QR-Erzeuger steht im Seitenskript (Bytemodus, Fehlerkorrektur M, bei langem Text L, Version 1 bis 10), eigene Umsetzung ohne Fremdcode. Geprüft mit dem QR-Dekoder von macOS: 18 Texte von 5 bis 271 Byte, alle fehlerfrei.
  - **Vom Nutzer bestätigt: Moblin und IRL Pro. Nicht geprüft: GoPro** (braucht die GoPro-Labs-Firmware, Hero 8 und neuer; die Schreibweise der Befehle stammt aus Moblins Quelltext und der Labs-Dokumentation). Rückmeldungen dazu bitte als Issue.
  - Manche Scan-Apps erkennen den `larix://`-Link nicht: dann die Kamera-App des Handys nehmen oder den Link in IRL Pro unter Einstellungen → Import/Export Settings → Import Settings einfügen.
- **Geändert: Beim Wechsel des Hauptbilds gibt es keine Rückfrage mehr** („Der Encoder startet dafür neu …“). Der Wechsel läuft sofort (Nutzerwunsch).
- Übersetzungen: die neuen Texte stehen in allen 14 Sprachen.

## 0.9.117 (Beta)
- **Neu: Reihenfolge der Kamera-Knöpfe ändern.** In der Kameraliste hat jede Kamera zwei Pfeile (▲ ▼); ein Klick tauscht sie mit dem Nachbarn in der angezeigten Liste. Die Kamera-Knöpfe in der Fußleiste am Handy folgen dieser Reihenfolge (bei „Alle Kameras immer bereit“ unter den gerade sendenden Kameras). Die Reihenfolge wird in `cameras.json` gespeichert.
  - Hauptbild, kleine Bilder, Rollen und die Vorschau bleiben unverändert (die Zuordnung der Bilder ist eine eigene Einstellung).
  - Technik: `POST /api/cameras/<id>` mit `{"swap_with": "<id>"}` (`CameraStore.swap` in `server.py`). Englische Texte in `web/i18n/en.json`, die anderen Sprachen folgen mit der nächsten Übersetzungsrunde.
  - Tests: `tools/test_ui_backend.py` (`CameraOrder`, 3 Tests), `tools/test_view.py` (Fußleiste folgt der Liste). Auf einer Box eingespielt und vom Nutzer geprüft (Pfeile und Fußleiste am Handy).

## 0.9.116 (Beta)
- **Behoben: Ein hochkantes Kamerabild (zum Beispiel ein iPhone im Hochformat) wurde als kleines Bild in den 16:9-Rahmen gequetscht**, in der Sendung und in der Vorschau. Als Hauptbild stimmte es schon (mittig mit Balken).
  - Jetzt gilt für ein hochkantes kleines Bild (Quelle höher als breit): Der Rahmen behält das Seitenverhältnis der Quelle bei der Höhe des 16:9-Rahmens, derselbe Platz (Ecke oder frei), kein Verzerren. Ein eingestellter Beschnitt gilt nur für Querformat und wird bei hochkantigen Quellen ignoriert; der Rahmen um das Bild folgt der neuen Größe.
  - Die Größe der Quelle kommt aus der nginx-Statistik (`meta/video/width|height`); `server.py` reicht sie mit der Kameraliste an die Seite weiter, die Vorschau zeichnet danach den hochkanten Rahmen.
  - Test: `tools/test_live.py` (`test_portrait_small_picture_keeps_its_aspect_ratio`). Auf einer Box mit einem iPhone im Hochformat geprüft: die Rahmengröße im Test und die Befehle an den Compositor; die Vorschau wurde gemeldet und angepasst, das Bild in OBS hat der Nutzer geprüft: passt.

## 0.9.115 (Beta)
- **Neu: Ladeanzeige (🔌) auch bei der Osmo Action 5 Pro und der Osmo Action 6.** Bisher zeigte die Box "am Ladekabel" nur bei der Osmo Action 4; bei den anderen stand "unbekannt". Gemessen am 6. Oktober 2026 auf der neuen Box (Kabel je einmal abgezogen und angesteckt, Statusmeldungen im Journal): Bei beiden steht der Strom an derselben Stelle wie bei der Action 4 (Bytes 5 bis 8), aus dem Akku etwa -600 bis -770 mA, beim Anstecken kurz um 0, am Kabel positiv (+700 bis +4400 mA). Dieselbe Schwelle (-100 mA) trennt beides.
  - Folge: Die **Akku-Warnung im Twitch-Chat** kommt für diese Kameras nicht mehr, solange sie am Kabel hängen, und wird beim Laden wieder scharf (die Warnung beachtet "lädt" schon immer, kannte es bei diesen Modellen aber nicht).
  - Grenzen: Je Modell nur ein Versuch. Die **Osmo 360** (teilt sich die Art "action5") bleibt ausgenommen, dort bleibt "unbekannt".
  - Tests: `tools/test_dji_service.py` (neuer Test mit den gemessenen Werten, die Tests für "unbekannt" laufen jetzt mit Modellen, bei denen es nicht gemessen ist).
- **Doku:** Die README beschreibt jetzt den aktuellen Stand (Version, "Alle Kameras immer bereit" mit der Compositor-Engine, Ladeanzeige, feste Verbindung der Kameras, Sperre über fremde WLANs, Messungen vom 6. Oktober, Grenzen, Lizenzen), die README des Ordners `belacoder/` nennt den vierten Patch.
- **Übersetzungen:** Die 35 neuen Texte seit 0.9.111 (Schalter "Alle Kameras immer bereit", Hinweise zu fehlendem `gst-launch-1.0`, Bedienhinweise der Kamera-Knöpfe, Update-Fortschritt, Netze in der Nähe, Tailscale-Karte, Verbindungsmeldungen) sind jetzt in allen 12 weiteren Sprachen da (maschinell übersetzt, Platzhalter und Programmnamen geprüft). Die Schwelle im Test für die Vollständigkeit steht wieder auf 98 %.

## 0.9.114 (Beta)
- **Übernommen: Pull Request #27 von Bittersweet1987 (Engine "Compositor" für "Alle Kameras immer bereit", Issue #19).** Einfügung durch ihn selbst am 6. Oktober 2026; die Beschreibung der Engine steht im folgenden Eintrag. Von uns dazu: Versionsnummer, englische Texte der neuen Bedienhinweise, Anpassung dreier Tests (Test der Fußleiste kennt den Info-Knopf, Umgebungsvariable `PIPBOX_LIVE` aus `tools/test_live.py` wirkt nur noch in dieser Datei, Schwelle für die anderen Sprachen vorübergehend 95 % bis zur Übersetzungsrunde) und der Hinweis zur Lizenz in `NOTICE.md` (der belacoder-Patch steht wie belacoder unter GPL-3.0).
- **Nicht von uns auf einer Box geprüft:** das Bauen des belacoder mit dem Patch und der Lauf der Engine mit echten Kameras. Bittersweet1987 hat es auf seiner Box (Orange Pi 5 Plus) mit drei Kameras gemessen; die Grenzen stehen unten. Der Schalter "Alle Kameras immer bereit" bleibt standardmäßig aus; ohne den Schalter ändert sich nichts.

## Vorschlag von Bittersweet1987 (Pull Request #27)
- Neu: **Engine "Compositor" für "Alle Kameras immer bereit".** Statt Zubringer-Prozessen, UDP und dem Füller `pbpipsel` hat die Sendekette jetzt EINE Pipeline, die sich nie ändert: alle vier Kameras als Zweige (`sbf0` bis `sbf3`), drei Rahmen, ein Compositor mit festem Ausgabeformat (`NV12 1920x1080 30/1`, immer lebendig durch die Rahmen-Streifen als Takt) und ein Ton-Umschalter mit Stille als Pad 0. Der Encoder sieht dadurch nur noch eine Sorte Bild und keinen Formatwechsel mehr (die Seitenfehler des Encoders `rk_iommu … Page fault` vom 5. Oktober 2026, die die Box abstürzen ließen, entstehen bei wechselnden Bildarten und Formaten; die Ursache ist eine begründete Vermutung, nicht bewiesen).
  - **Kameras kommen und gehen ohne Neustart:** Alle Zweige starten gestoppt, die Sendung beginnt mit Schwarz und Stille. Sendet eine Kamera, startet der Regler ihren Zweig (`feed sbf<N> on`) und blendet das Bild ein, sobald Bilder ankommen. Fällt sie aus, stoppt der Zweig und das Bild verschwindet sofort.
  - **Tauschen, Größe, Ecke, Zuschnitt, Rahmen, Ton, Stumm, Verzögerung und sogar ein anderer Schlüssel an einem Platz gehen im Betrieb ohne Neustart** (Pad-Eigenschaften des Compositors über den Steuerkanal von belacoder; ein neuer Schlüssel: Zweig stoppen, Adresse setzen, Zweig starten). Das Bild, das oben landet, wird zuletzt bewegt.
  - **Deaktivierte Kameras** (Doppelklick) sind nicht im Stream (ihr Zweig läuft nicht), behalten aber ihren Knopf in der Fußleiste, solange sie senden, und sind keine Tonquelle. Ein kurzer Klick auf eine deaktivierte Kamera aktiviert sie mit ausgeblendetem Bild (Ton nutzbar).
  - **Nach einem Neustart des Encoders** (Stall-Wächter) sendet der Regler alle Live-Einstellungen erneut, sonst bliebe das Bild schwarz.
  - Der Dienst bindet belacoder an die schnellen Kerne (`taskset`, über `cpu_capacity` erkannt) und stellt deren Governor auf `performance`, solange gesendet wird (danach zurück; Zustandsdatei, falls der Dienst abstürzt). `GST_MPP_NO_RGA=1` gilt nur für diese Engine.
  - **Fußleiste am Handy:** Info-Knopf (ⓘ) mit der Bedienung der Kamera-Knöpfe; die Knöpfe bleiben beim Start sichtbar, "Live" springt nicht mehr.
  - Neue Dateien: `pipbox_live.py` (Engine: Pipeline-Text, Befehle, Regler), `belacoder/belacoder-live-feeds.patch` (Kamera-Zweige `sbf0` bis `sbf7`, Steuerkanal `-C`, Statistik `-S`, Ausrichtung `-A`, Antwort auf die Latenzabfrage; wird von `build.sh` nach deinen drei Patches angewendet, Version `…-jt2-sb11`), `tools/test_live.py` (37 Tests), `tools/analyze_rec.py` (Auswertung eines Mitschnitts). Die Engine gilt nur, wenn der installierte belacoder `-sb10` oder neuer ist; sonst läuft die bisherige Zubringer-Variante weiter. Der Schalter bleibt, der normale Modus ist unverändert.
  - Der belacoder-Teil stammt aus dem Projekt streamingbox (Bittersweet1987, GPL-3.0 wie belacoder); der Python-Teil ist neu geschrieben.
- **Ehrlich zu den Grenzen:** Mit drei echten Kameras (Osmo, Handy, iOS) und zwei Test-Quellen auf einer Orange Pi 5 Plus (0.9.113) gemessen: Ausgang 30 Bilder/s, kein `rk_iommu`/RGA-Fehler im Kernel-Log, kein Absturz. **Nicht gelöst:** Beim Tauschen steht der Ausgang etwa 1 s (die Ursache liegt vermutlich bei der Neuverhandlung nach einer Größenänderung des Skalierers); der Ton hat 1 bis 2 s Lücken, wenn der DJI-Ton stoßweise kommt; der Stall-Wächter hat in einer Sitzung zwei Mal den Encoder neu gestartet (vor Verbesserungen am Puffer); der echte Weg über `srtla_send` zum Server wurde nur in Teilen im Betrieb gesehen. Der Build mit dem Commit `ccce9ca` (`build.sh`) wurde nur übersetzt, die Laufzeit-Tests liefen mit einer Fassung auf dem Stand `4956dbf`.

## 0.9.113 (Beta)
- **Behoben: Eine Kamera bleibt bei der gewählten Verbindung (z. B. dem mobilen Router) und wechselt nicht still ins Heimnetz.** Vorfall am 6. Oktober: Bei beiden DJI-Kameras war der Name `eth0` als Verbindung gespeichert. Nach einem Neustart meinte `eth0` das Heimnetz (192.168.178.x) statt des Routers (192.168.80.x). Die Box schickte den Kameras die Heimnetz-Adresse, die aus dem Router-WLAN nicht erreichbar ist, und die Streams fielen nach wenigen Sekunden aus. Netzwerknamen (`eth0`, `eth1`, `eth2`) sind nach einem Neustart nicht immer gleich vergeben; in der Merkliste der Box standen alle drei für denselben Router.
  - Die Box merkt sich jetzt zur gewählten Verbindung zusätzlich die **Hardware-Adresse (MAC)** der Netzwerkkarte, die beim Start gleich bleibt. Gespeichert wird sie, wenn du in der Oberfläche eine Verbindung wählst, beim Hinzufügen einer Kamera und bei Kameras aus älteren Versionen, sobald ihr Stream wirklich ankommt.
  - Ist die Karte der gewählten Verbindung nicht da (Router aus, Kabel ab), **gibt es einen klaren Fehler statt eines Ausweichens** auf eine andere Verbindung: "Die gewählte Verbindung (Router) ist nicht da. … Die Kamera bleibt bei dieser Verbindung und wechselt nicht ins Heimnetz." Sobald der Router wieder da ist, verbindet die Kamera von selbst.
  - Die Oberfläche zeigt den aktuellen Namen der gewählten Verbindung, auch wenn er sich geändert hat. Die Hardware-Adresse selbst wird nicht angezeigt und lässt sich nicht von außen setzen.
  - Grenzen: Eine Kamera, deren Verbindung schon vor diesem Update falsch gespeichert war, muss einmal richtig gewählt werden (oder erst streamen, dann wird sie festgehalten). Mit echten Kameras und einem echten Neustart mit vertauschten Namen nicht geprüft, nur mit Tests (`tools/test_conn_pin.py`, 11).
- Dazu: `tools/analyze_stream.py` wertet eine Aufzeichnung des Ausgangs Bild für Bild aus (Bilder je Sekunde, Lücken, schwarze Bilder, Standbilder, fehlende kleine Bilder).

## 0.9.112 (Beta)
- **Behoben (Meldung von Bittersweet1987 zu 0.9.111, Issue #19): Mit "Alle Kameras immer bereit" ging kein Stream raus.** Ursache: Die Zubringer brauchen das Programm `gst-launch-1.0` (Paket `gstreamer1.0-tools`), das ein frisches BELABOX-Image nicht mitbringt. Auf der Entwicklungsbox war es von Hand installiert, deshalb fiel das beim Testen nicht auf. Im Protokoll stand "Zubringer 0 nicht startbar (FileNotFoundError)".
  - Der Installer (Software-Update) installiert `gstreamer1.0-tools` jetzt mit (bei Fehlschlag nach `apt-get update` noch einmal). Das braucht auch die HDMI-Kamera.
  - Fehlt das Programm trotzdem (oder der Baustein ist zu alt), **läuft die Sendung im normalen Modus weiter** statt Schwarz zu senden, und die Karte "Live" zeigt den Grund ("Alle Kameras immer bereit ist nicht aktiv: …"). Die Meldung im Protokoll kommt nur noch einmal je Zubringer und nennt das fehlende Paket.
- Neu: `tools/boxtest_always_send.py`, ein Gesamttest mit dem **echten Sende-Dienst und dem echten belacoder** (statt Dateiausgabe): Der Encoder liefert über 60 s ohne Neustart Daten an die SRT-Seite, Hauptkamera fällt aus, Ersatz übernimmt, Rückkehr nach 3 s stabilem Bild. Auf der Box mit Testbildern gelaufen (kein Netz, srtla_send ersetzt durch einen Platzhalter); **nicht mit echten Kameras und nicht über SRTLA ins Internet.**
- Tests: `tools/test_always.py` (+4, 49).

## 0.9.111 (Beta)
- Neu (Issue #19, Anregung von Bittersweet1987, Umsetzung eigen): **"Alle Kameras immer bereit" (Beta).** Neuer Schalter im Bildaufbau bei "Bild in Bild" (Standard **aus**; ohne den Schalter ändert sich nichts gegenüber 0.9.110). Eingeschaltet gilt:
  - Jedes Bildfeld der Sendekette hat immer einen Eingang. Sendet eine Kamera nicht, steht an ihrer Stelle ein schwarzes Bild (Ton: Stille), unsichtbar, ohne Rahmen.
  - **Kameras können jederzeit dazukommen, ausfallen und zurückkehren, ohne dass die Sendung neu startet.** Tauschen, Ein- und Ausblenden und der Ton-Wechsel gehen ebenfalls ohne Neustart. Einen "Notbetrieb" gibt es in diesem Modus nicht mehr.
  - **Fällt die Hauptkamera aus, übernimmt die nächste Kamera mit echtem Stream** (Reihenfolge: kleines Bild 1, 2 …). Kommt die ursprüngliche zurück, wird sie nach 3 s stabilem Bild wieder Hauptbild; die Ersatzkamera geht zurück auf ihr eigenes Bild. Wechsel, die du selbst einstellst, gelten sofort.
  - Fußleiste und Tonauswahl zeigen nur Kameras mit aktivem Stream.
  - Die Sendung startet, sobald **eine** eingestellte Kamera sendet; die anderen kommen später dazu.
- Technik: Je Kamera holt ein kleiner Zubringer (`gst-launch-1.0`, `pipbox_always.py`) den Stream aus dem RTMP-Server und reicht Bild (H.264 per RTP) und Ton (PCM) lokal an die Sendekette weiter (UDP, Loopback, Ports ab 9410); endet der Stream, startet der Zubringer nach 1 s neu. Die Sendekette hat dadurch nie eine Quelle, die ausfällt. Der Baustein `pbpipsel` (`gst/gstpbpip.c`, wird beim Update neu gebaut) füllt fehlende Bilder mit Schwarz (nach 0,5 s) und Ton mit Stille (nach 0,15 s) und lässt die Zeit nie rückwärts laufen; `pbctl` meldet in `/run/pipbox-send/cam-live`, welche Kamera Bilder liefert. Der Sende-Dienst (`pipbox_send.py`) wählt alle 0,5 s Haupt- und kleine Bilder.
- Gemessen **auf der Box mit Testbildern** (drei synthetische 1080p30-Kameras, keine echten): keine Neustarts über 55 s; Hauptkamera A stoppt, B wird nach rund 2 s Hauptbild; A kehrt zurück und ist nach rund 4,6 s wieder Hauptbild; zugeschaltete Kamera erscheint ohne Neustart; Bild etwa 30 Bilder/s mit kurzen Einbrüchen (22 bis 27 Bilder/s für etwa 1 s) an den Umschaltstellen; Ton lückenlos. Rechenlast mit drei Kameras: Sendekette etwa 40 % eines Kerns, je Zubringer etwa 5 %.
- **Grenzen (ehrlich):** Mit **echten Kameras und einer echten Sendung nicht geprüft.** Jede Kamera wird in diesem Modus doppelt dekodiert (groß und klein), das kostet mehr Rechenleistung. Fällt die letzte Kamera aus, sendet die Box Schwarz mit Stille weiter. Ist die Kamera mit dem gewählten Ton ausgefallen, kommt Stille (kein Ausweichen auf eine andere Kamera). Ein zusätzliches kleines Bild, das erst nach dem Start der Sendung eingestellt wird, braucht weiter einen Neustart (andere Bauform der Kette).
- Tests: `tools/test_always.py` (45, Zubringer, Platzzuordnung, Wahl bei Ausfall und Rückkehr, Pipeline-Text, Einstellungen, Fußleiste, Sende-Dienst); Box-Tests `tools/boxtest_fill.py` (Füllen im Baustein) und `tools/boxtest_always.py` (ganze Kette mit Testkameras). Entwurf: `KONZEPT-immer-bereit.md`.
- Englischer Text für den Schalter ergänzt (andere Sprachen folgen am Schluss).

## 0.9.110 (Beta)
- Geändert (Issue #24, Rückmeldung des Melders): **Die Karte "Language · Sprache" am Ende der Seite entfällt.** Die Sprachauswahl steht schon im Kopf der Seite (und beim ersten Öffnen als Auswahl); die zweite Auswahl unten war doppelt und blähte das Menü auf. Die Sprache lässt sich weiter im Kopf wechseln, auf der Anmeldeseite wie bisher.
  - Geprüft in der Demo: Seite lädt ohne Fehler, Sprachwechsel im Kopf geht. Nicht auf einem Handy geprüft.


## 0.9.109 (Beta)
- **Testversion auf Wunsch: Der Tailscale-Teil ist wieder auf dem Stand von 0.9.94.** Anlass: Nach einem Neustart war die Box mit 0.9.94 (und frisch angemeldetem Tailscale) sofort öffentlich erreichbar, mit 0.9.107 (ebenfalls frisch angemeldet) nicht. Wir wollen sehen, ob es an unserem Tailscale-Teil liegt.
  - Zurückgesetzt auf 0.9.94: der Helfer `install/pipbox-remote.py` (damit entfallen die mehrfachen Versuche beim Laden der Paketliste aus 0.9.100 und das Auswerten des Freischalt-Links bei Zeitüberschreitung aus 0.9.101), die Klasse `Remote` in `server.py` und die Karte "Fernzugriff" (damit entfällt die Prüfung der öffentlichen Adresse aus 0.9.108).
  - **Folgen:** Auf einer Box ohne Tailscale kann die Installation wieder mit "Unable to locate package tailscale" scheitern, und "Freigeben" kann wieder mit "timed out after 40 seconds" enden, wenn Serve oder Funnel im Konto noch nicht freigeschaltet sind. Wer Tailscale schon eingerichtet hat, merkt davon nichts.
  - Unverändert bleiben alle anderen Neuerungen (auch die Tailscale-Zeilen im Protokollpaket und die Tailscale-Aufnahmen beim Update).
  - **Erwartung ehrlich gesagt:** Der Tailscale-Teil läuft nur, wenn man in der Karte etwas drückt oder die Karte öffnet, nicht beim Start der Box. Ich erwarte daher keinen Unterschied beim Neustart. Der Test zeigt, ob das stimmt.
  - Die zugehörigen Tests (`tools/test_remote_install.py`, `tools/test_remote_public.py`) sind dabei zu Platzhaltern geworden; die alten stehen in der Git-Historie (0.9.108).


## 0.9.108 (Beta)
- Geändert: **Die Karte "Fernzugriff" meldet die öffentliche Freigabe (Funnel) erst als fertig, wenn die öffentliche Adresse wirklich antwortet.** Bisher stand "freigegeben", sobald die Einstellung gesetzt war, obwohl Tailscale nach dem Anmelden oder Umschalten erst Zertifikat und Adresse im Internet vorbereitet. Die Adresse ging dann noch eine Weile nicht, und die Karte sagte trotzdem "freigegeben".
  - Die Box prüft jetzt selbst: Sie löst den Namen über einen öffentlichen Namensdienst (1.1.1.1, dann 8.8.8.8) auf, verbindet sich mit dem Zertifikat der Adresse und ruft die Startseite ab (Weg: von der Box ins Internet zu Tailscale und zurück). Das Ergebnis erscheint in der Karte: "Die öffentliche Adresse wird geprüft …", "… antwortet noch nicht. Tailscale bereitet gerade Zertifikat und Adresse vor … Bitte nicht aus- und wieder einschalten" oder "… antwortet." Solange sie nicht antwortet, steht im Kopf "wird vorbereitet" statt "freigegeben". Bis zur ersten Antwort wird alle 8 Sekunden geprüft, danach alle 2 Minuten.
  - Geprüft werden nur Namen auf `.ts.net`. Die Prüfung ändert nichts an der Freigabe und läuft nur, wenn Funnel an ist.
  - **Grenzen:** Das ist die Sicht von der Box aus. Ein Gerät mit eigenem Zwischenspeicher für Namen oder in einem anderen Netz kann die Adresse früher oder später erreichen. Es sagt nicht, wie lange Tailscale braucht, nur ob es geht. Geprüft mit Tests (DNS-Antworten, Zustände, Statusfeld) und gegen echte Namensdienste und eine echte HTTPS-Adresse (github.com); **nicht mit einer echten öffentlichen Tailscale-Adresse**.
  - Tests: `tools/test_remote_public.py` (8).


## 0.9.107 (Beta)
- **Testversion für die Fehlersuche bei Tailscale: ändert für den Betrieb nichts.** Beim Einspielen dieser Version schreibt `install.sh` den Zustand der Tailscale-Freigaben (`tailscale serve status` und `tailscale funnel status`) **vor** und **nach** der Installation ins Protokoll der Software-Updates (Karte "Protokolle", Abschnitt "Protokoll der Software-Updates"). So lässt sich ablesen, ob eine öffentliche Freigabe (Funnel) bei einem Update verloren geht. Die Befehle lesen nur und ändern nichts; fehlt Tailscale oder antwortet es nicht, geht die Installation unverändert weiter.
  - Tests: `tools/test_swupdate_progress.py` (+3).


## 0.9.106 (Beta)
- Neu: **Das Protokollpaket (Karte "Protokolle") enthält jetzt den Zustand der Tailscale-Freigaben** (`tailscale serve status` und `tailscale funnel status`) und aus dem Journal von Tailscale nur die Zeilen zu Freigabe, Zertifikat, Anmeldung und Fehlern (der Rest ist Rauschen). Anlass: Auf einer Box ging die öffentliche Freigabe (Funnel) angeblich immer wieder verloren; im bisherigen Paket stand dazu nichts vom Tailscale-Dienst, nur das Protokoll unseres Helfers (dort: Funnel an um 14:32, danach nur noch von Hand "aus" und "an" um 15:10 und 15:59 UTC, nichts von selbst).
  - **Die Ursache für das Verlieren der Freigabe ist damit nicht gefunden oder behoben**: Unser Code beendet Funnel nie von selbst; nur der Knopf "Beenden" tut das. Das neue Paket soll zeigen, ob die Freigabe in Tailscale wirklich verschwindet (dann steht es in `funnel status`) und was der Dienst dazu meldet. Die Namen und Adressen sind wie bisher bereinigt.
  - Test: `tools/test_logs.py` (+1).


## 0.9.105 (Beta)
- Geändert: **Die Verbindung einer DJI-Kamera (Auswahl der Verbindung, Netze in der Nähe, gespeicherte Netze, WLAN-Name, Passwort, Adresse, "Löschen") lässt sich jetzt jederzeit ändern, auch wenn die Kamera verbunden ist oder streamt.** Vorher war der ganze Bereich gesperrt, solange die Kamera verbunden war ("Zuerst trennen"), und bei einer Kamera, die nicht gefunden wird, zusätzlich jede zweite Viertelminute. Der Bereich "Bild und Stream" war nie gesperrt.
  - Eine Änderung gilt **ab der nächsten Verbindung** der Kamera; die laufende bleibt bestehen. Ist die Kamera verbunden, steht darüber ein Hinweis.
  - Ist die Kamera verbunden, werden Name, Passwort und Adresse erst beim Verlassen des Feldes gespeichert (sonst bei jedem Tastendruck), damit eine Kamera, die sich nach einem Abriss neu verbindet, nie einen halb getippten Netznamen liest.
  - Gesperrt bleibt nur die Art ("Nur Akkustand lesen" oder Stream), solange eine Sitzung läuft.
  - Tests: `tools/test_dji_service.py` (Netz in jedem Zustand änderbar, gespeichertes Netz benutzen und löschen beim Streamen, Art bleibt gesperrt). **Mit einer echten Kamera nicht geprüft.**


## 0.9.104 (Beta)
- Neu: **Fortschrittsanzeige bei beiden Updates** ("Software-Update (IRL4YOU BOX)" und "System-Updates (BELABOX)") im selben Aussehen: Balken mit laufender Bewegung, Prozent, aktueller Schritt und laufende Zeit (⏱ 0:42). Die Zeile mit der Zeit zählt weiter, auch wenn gerade nichts Neues gemeldet wird, damit man sieht, dass etwas läuft.
  - IRL4YOU-Update: Schritte Laden (nach Bytes, 2 bis 20 %), Archiv prüfen, Sichern, dann die Schritte von `install.sh` (Pakete prüfen, Dienst anhalten, Dateien kopieren, Bild-Baustein, SRTLA-Sender, Encoder, Dienste starten; `install.sh` meldet sie mit Zeilen `PIPBOX-STEP <Name>`). Wechsel auf eine andere Version und "Zurück" zeigen ebenfalls einen Balken, mit weniger Schritten.
  - Die Prozente sind **grobe Stufen**, keine Zeitschätzung: Das Neubauen von Bild-Baustein, SRTLA-Sender und Encoder (nur wenn sich ihre Quellen geändert haben) kann einige Minuten dauern; der Balken steht dann beim Schritt und die Zeit läuft weiter.
  - System-Updates (BELABOX): wie bisher aus den Zählern von apt (Laden, Entpacken, Einrichten), jetzt im gleichen Balken.
- Geändert: **Die Seite lädt erst nach dem Ende des Updates neu** und zeigt dann bei Bedarf die Anmeldung. Beim Update startet die Oberfläche neu und kennt die Anmeldung dieses Browsers evtl. nicht mehr; früher blieb die Seite dann bei "Update verfügbar" stehen. Jetzt zeigt sie den Fortschritt weiter (neuer Abruf `/api/swprogress`, ohne Anmeldung, nur Zustand, Schritt und Prozent, keine Version und keine Adressen) und lädt nach "fertig" einmal neu. Schlägt das Update fehl, steht das in der Karte, und die Seite lädt nach ein paar Sekunden neu.
  - Außerdem lädt die Seite neu, wenn die Box eine andere Version meldet als beim Öffnen und nichts mehr installiert wird (auch bei einem Update von einem anderen Gerät), und bei jeder Antwort "nicht angemeldet" außerhalb eines Updates (dann erscheint die Anmeldeseite).
  - **Wichtig:** Das gilt erst, wenn diese Version auf der Box läuft. Das Update **auf** 0.9.104 macht noch die alte Seite mit dem alten Helfer: ohne Fortschrittsschritte, und die Seite muss danach evtl. von Hand neu geladen werden. Ab dem Update danach läuft alles wie beschrieben.
  - Geprüft in der Demo (vorgetäuschtes Update: Balken, Schritte, Zeit, Neuladen am Ende; Abruf ohne Anmeldung nachgestellt) und mit Tests (`tools/test_swupdate_progress.py` neu, `tools/test_ui_backend.py` erweitert). **Nicht mit einem echten Update auf der Box geprüft.**
- Die englischen Texte sind dabei; die anderen Sprachen folgen am Schluss.

## 0.9.103 (Beta)
- Behoben: **Die Felder der Verbindung einer DJI-Kamera ("Netze in der Nähe", "Netze suchen", WLAN-Name, Passwort) ließen sich oft nicht anklicken.** Ursache (auf der Box abgelesen): Wird eine Kamera nicht gefunden, sucht sie alle 30 Sekunden neu (15 s "Sucht", dann 15 s "Fehler"). Während des Suchens waren die Felder gesperrt, die Eingabe also nur jede zweite Viertelminute möglich, und ein angefangener Eintrag wurde gesperrt. Jetzt sind die Felder beim Suchen und beim Aufbau der Bluetooth-Verbindung frei; gesperrt bleiben sie ab dem Koppeln, solange die Kamera verbunden ist oder sendet (wie bisher; der Hinweis "Zuerst trennen" steht dann über den Feldern).
  - Dazu liest die Kamera-Sitzung Netz, Passwort und Adresse **kurz vor der Übergabe an die Kamera noch einmal** (vorher nur beim Start der Sitzung, vor dem Suchen). Eine Änderung während des Suchens gilt also noch für diesen Versuch.
  - Die Art (Stream oder "Nur Akkustand lesen") bleibt gesperrt, solange eine Sitzung läuft, auch beim Suchen, weil die Sitzung sie beim Start liest.
  - Tests: `tools/test_dji_service.py` (+2, 119): Sperre je Zustand, Netz-Änderung beim Suchen wird verwendet. **Mit einer echten Kamera nicht geprüft.**

## 0.9.102 (Beta)
- Neu: **Netze in der Nähe zur Auswahl beim Einrichten einer DJI-Kamera.** Im Abschnitt "Verbindung" der Kamera (bei "Manuell" oder jeder Verbindung ohne eigenes WLAN der Box) gibt es unter "Gespeicherte Netze" jetzt **"Netze in der Nähe"**: eine Auswahlliste der WLANs, die die WLAN-Karte der Box zuletzt gefunden hat (nach Signalstärke, mit Schloss bei verschlüsselten Netzen). Netz wählen, Passwort eingeben, fertig. Ist das Netz schon gespeichert, gilt sein gespeichertes Passwort. Der Knopf "Netze suchen" über der Liste sucht neu.
  - Gesucht wird **im Vorfeld einmal von selbst**, sobald eine Kamera ein eigenes WLAN braucht und noch keine Suche vorliegt, und nur, wenn gerade nicht gesendet wird (danach höchstens alle 5 Minuten wieder, wenn die Liste leer blieb). Eine Suche kann eine laufende WLAN-Verbindung der Box kurz stören. Läuft auf der Karte ein Hotspot, sucht sie nicht (dann bleibt die Liste leer; die Karte "Verbindungen" nennt den Grund).
  - Die Liste zeigt, was die **Box** sieht, nicht die Kamera. Beide stehen aber meist nebeneinander; ein Netz auf 5 GHz sieht die Kamera nur, wenn sie das kann.
  - Aussehen: Alle Felder dieses Abschnitts sind gleich breit (die Knöpfe "Löschen" und "Netze suchen" stehen jetzt in der Beschriftungszeile statt neben dem Feld) und stehen zweispaltig; bei schmalem Bildschirm (bis 560 Pixel) einspaltig. Am Handy mit 360 Pixel Breite und am Desktop in der Vorschau (Demo) geprüft, **nicht mit einer echten Box und Kamera**.
  - Tests: Die Texte sind Deutsch und Englisch; die anderen Sprachen folgen am Schluss (Schwellwert in `tools/test_i18n.py` auf 99 Prozent).

## 0.9.101 (Beta)
- Behoben: **"Freigeben" im privaten Tailscale-Netz brach mit "timed out after 40 seconds" ab.** Ist "Serve" (HTTPS) im Tailscale-Konto noch nicht freigeschaltet, gibt `tailscale serve` einen Link aus und wartet dann auf die Freischaltung. Die Box wartete 40 Sekunden, brach ab und warf die Ausgabe samt Link weg; angezeigt wurde nur die englische Fehlermeldung von Python. Jetzt wird die bis dahin gelesene Ausgabe ausgewertet: Die Oberfläche zeigt wie vorgesehen den Hinweis mit dem Link ("Öffne den Link, lasse Funnel AUS und klicke Enable HTTPS. Danach erneut freigeben."). Dasselbe gilt für die öffentliche Freigabe (Funnel).
  - Annahme: Die Meldung kam, weil Serve im Konto noch nicht freigeschaltet war. Das ist aus der Meldung abgeleitet, **nicht an dieser Box geprüft**. Mit Tests (`tools/test_remote_install.py`, jetzt 7) nachgestellt, nicht gegen ein echtes Tailscale-Konto, in dem Serve noch aus ist.

## 0.9.100 (Beta)
- Behoben: **Die Installation von Tailscale schlug auf manchen Boxen mit "Unable to locate package tailscale" fehl.** Die Paketliste der Tailscale-Quelle wurde ein einziges Mal geladen und das Ergebnis nicht geprüft; war apt gerade gesperrt (automatische Updates nach dem Start) oder schlug das Laden fehl, kannte apt das Paket nicht. Jetzt wird geprüft, ob apt das Paket kennt, und bis zu vier Mal im Abstand von 15 s wiederholt, zuletzt mit der ganzen Paketliste statt nur der Tailscale-Quelle. Bleibt es dabei, nennt die Meldung die Ausgabe von apt.
  - Die genaue Ursache auf der betroffenen Box ist nicht bekannt (die Ausgabe von apt wurde nicht mitgelesen). Auf der eigenen Test-Box läuft das Laden der Paketliste ohne Fehler. **Nicht an einer Box geprüft, bei der der Fehler auftrat**; nur mit Tests (`tools/test_remote_install.py`, 4).
- Doku: `NOTICE.md` und `belacoder/README.md` nennen jetzt alle drei belacoder-Patches (GPL-3.0) und haben einen Abschnitt "Welche Lizenz gilt wo".

## 0.9.99 (Beta)
- Neu (Issue #25): **Schalter "Über fremde WLANs sperren"** (Karte "Verbindungen", Bereich "Zugriff auf diese Oberfläche"). Die Oberfläche ist unverschlüsselt (HTTP); in einem WLAN, in dem die Box nur Gast ist (Hotel, Handy-Hotspot, fremdes Netz), liest dort jeder mit. Ist der Schalter an, werden Verbindungen auf der Adresse eines solchen WLANs ohne Antwort getrennt. Ethernet, der eigene Hotspot der Box, USB und Tailscale bleiben erreichbar. Standard: aus.
  - Erkennung über NetworkManager (WLAN verbunden, Modus nicht "ap"); bei einem Fehler wird nichts gesperrt. Die Liste wird nur gefragt, wenn der Schalter an ist (5 s zwischengespeichert).
  - **Schutz vor dem Aussperren:** Wer gerade selbst über ein Gast-WLAN verbunden ist, kann den Schalter nicht einschalten (Meldung mit dem Hinweis auf Ethernet, Hotspot oder Tailscale). Ausschalten geht von überall. Wer sich trotzdem aussperrt: Schalter über Ethernet/Tailscale ausschalten oder die Datei `ui-access.json` im Zustandsordner löschen.
  - Gilt für alle WLANs, in denen die Box Gast ist, auch für den eigenen Handy-Hotspot (das ist ein Gast-WLAN).
  - Tests: `tools/test_uiaccess.py` (12). **Nicht an einer echten Box mit Gast-WLAN geprüft** (die Box war ohne WLAN verbunden); die Erkennung nutzt dieselben nmcli-Abfragen wie die Kamera-Verbindungen.

## 0.9.98 (Beta)
- Sicherheit, letzte Kleinigkeit zu Issue #25: Eine abgeschnittene oder zu langsame Anfrage schrieb Tracebacks ins Journal (ValueError "Die Anfrage ist unvollständig", danach BrokenPipeError beim Antworten). Abgebrochene Verbindungen und Zeitüberschreitungen des Gegenübers werden jetzt still verworfen, echte Fehler des Dienstes bleiben sichtbar. Test: `QuietErrors` in `tools/test_security.py`.

## 0.9.97 (Beta)
- Korrigiert: **NPU-Auslastung** in "Details" zeigte immer 100 %. Der Frequenzregler der NPU (rknpu_ondemand) meldet auch im Leerlauf 100 %; die echte Last (0 %) steht nur in debugfs und das liest nur root. Jetzt steht dort "–" mit Erklärung (Tooltip) und die Frequenz; die GPU-Auslastung stimmt (Mali-Regler).
- Mit der echten Box geprüft (nach 0.9.96): Die Kennzahlen-Datei von `belacoder` liefert Bitrate, RTT, Sendepuffer, Neuübertragungen, Verlust und 29 bis 30 Bilder pro Sekunde; Temperaturen, GPU, Speicherplatz stimmen. Beobachtung: Neuübertragungen und Verlust lagen kurz nach dem Start bei etwa 5 % (Summe seit Verbindungsbeginn).

## 0.9.96 (Beta)
- Neu (Issue #26): **Details im Status.** Unter den Kästen des Status steht ein zugeklappter Bereich "Details" (gezeichnet nur, solange er offen ist) mit vier Kategorien:
  - **Senden:** Bitrate (gemessen und eingestellt), Laufzeit (Ping, RTT) gesamt und je Weg, Sendepuffer (ms und Pakete), Neuübertragungen und Paketverlust (Summe und Anteil), Encoder-Bilder pro Sekunde.
  - **Prozessor:** Auslastung und Takt je Kern (0 bis 7), GPU und NPU. **Temperaturen:** je Wärmezone (SoC, große Kerne, kleine Kerne, Mitte, GPU, NPU). **Speicherplatz:** belegt, frei, gesamt.
  - Die Sendewerte kommen von `belacoder` (neuer Patch `belacoder/belacoder-stats.patch`: schreibt einmal je Sekunde eine JSON-Datei im RAM, nur wenn der Sender sie anfordert). Das Update baut `belacoder` dafür neu (braucht Internet, git, gcc); bis dahin und ohne Sendung steht "Keine Daten". Die Kernwerte, Temperaturen, GPU/NPU und Speicherplatz zeigen sich sofort.
  - Hinweis: Neuübertragungen und Verlust sind Summen seit dem Start der Verbindung. Die Temperaturen sind je Wärmezone, nicht je einzelnem Kern (der Chip hat keine Messung je Kern). Die NPU-Last kommt aus dem Frequenzregler des Chips.
  - Tests: `tools/test_details.py` (7).

## 0.9.95 (Beta)
- Geändert: **Ton-Wechsel mit kaum noch Stille.** Der DJI-Ton kommt stoßweise (Stücke von bis zu 1,7 s). Die Version 0.9.94 füllte die Lücke bis zum nächsten passenden Stück mit Stille (gemessen 0,85 s zum kleinen Bild, 1,7 s zurück). Jetzt spielt die alte Quelle weiter, bis ein Puffer der neuen passt, und schließt dann nahtlos an (kurze Aus- und Einblendung, wie bisher). "Passt" heißt: Der neue Puffer liegt höchstens 0,4 s vor und höchstens 50 ms nach dem Ende des alten (zu spät würde der Ton hinter die Echtzeit fallen und den Muxer das Bild zurückhalten lassen, gemessen schon bei 0,4 s; zu früh bedeutet nur einen Ton-Versatz bis 0,4 s zum Bild). Kommt in 2,5 s nichts Passendes, springt der Ton auf die Echtzeit-Lage und die Lücke wird mit Stille gefüllt (bis 12 s).
  - Der "typische" Zeitabstand je Tonweg ist jetzt ein gleitender Mittelwert (10 s) statt des höchsten Werts; der höchste Wert war ein Ausreißer und machte einen Weg dauerhaft bis 1,8 s "zu spät". Fester Bezug statt Kette von Wechsel zu Wechsel: nichts summiert sich auf.
  - Gemessen (echter belacoder mit den echten Kameras, 30 Wechsel in 4,6 min): Bild größte Lücke 0,40 s, Ton lückenlos. Auf der laufenden Sendung (Mitschnitt beim Empfänger, 4 Wechsel mit Stummschaltung als Zeitmarke): keine Stille außer einer von 0,26 s, Bild größte Lücke 0,23 s; vorher 0,85 s und 1,7 s Stille.
  - Ein einzelner Lauf (nach einem Neustart der Sendung) hatte 2,9 s Stille und einen Ton-Ausfall von 5,8 s; er trat in vier Wiederholungen nicht mehr auf und ist nicht geklärt (der Fall "alter Eingang steht" wird jetzt mit Stille bis 12 s statt 3 s abgedeckt).
- Sicherheit (Nachtest zu Issue #25 von romestylez, 0.9.94): **Gesamtfrist für die Anfrage** (Kopfzeilen und Inhalt zusammen 15 s, danach wird die Verbindung getrennt; vorher galten die 15 s je Lesevorgang, ein Byte alle paar Sekunden hielt sie offen). **Hinter dem Tailscale-Proxy** (Adresse 127.0.0.1) zählt der echte Absender aus X-Forwarded-For (höchstens 16 Verbindungen je Absender, vor dem Lesen der Kopfzeilen höchstens 48 vom Proxy), sonst teilten sich alle Zugriffe über Serve/Funnel 16 Plätze. Tests in `tools/test_security.py`.

## 0.9.94 (Beta)
- Behoben: **Ton-Wechsel zwischen Kameras ruckelt nicht mehr und knackt nicht mehr.** Mitschnitt des Empfängers bei 10 Wechseln mit zwei DJI-Kameras (Verzögerung 0 / 1860 ms): nach jedem Wechsel zum Hauptton fehlten 1,8 s Ton und das Bild stand etwa 8 s fast still (alle 1,6 s ein Bild). Ursache: Der Umschalter verwarf beim Wechsel Ton des neuen Wegs oder ließ eine Zeitlücke; der Muxer wartet dann auf den Ton und hält das Bild zurück.
  - Jetzt bleibt der Ton-Ausgang ohne Lücke: Der letzte Puffer der alten Quelle wird ausgeblendet, eine nötige Lücke wird mit Stille (höchstens 3 s) gefüllt, der erste Puffer der neuen Quelle eingeblendet (je etwa 21 ms, "leichte Ausblendung"). Hörbar bleibt höchstens ein kurzes Absenken beim Wechsel.
  - Gemessen: echter `belacoder` mit den echten Kameras der Box, 24 Ton-Wechsel in 4 Minuten: größte Lücke im Bild 0,27 s (einmal), im Ton 0,25 s (nur beim Beenden); vorher mehrfach 1,6 bis 1,8 s. Synthetischer Test mit stoßweisem Ton: vorher 11 Knackser (Sprünge bis Vollaussteuerung) und 2,6 s Lücken, jetzt keine Knackser und keine Lücken.
  - Auf der laufenden Sendung (Mitschnitt beim Empfänger, 10 Wechsel im 10-s-Takt): Ton ohne Lücke, größte Lücke im Bild 0,03 s; ein erster Lauf direkt nach dem Neustart zeigte zwei Episoden mit ~0,6 s Bildlücken, die im Wiederholungslauf nicht mehr auftraten (vermutlich Anlaufphase oder Netz, nicht geklärt).
  - Tests: `tools/boxtest_synth_audio_switch.py` (ohne Hardware, auch neben einer laufenden Sendung), `tools/boxtest_switch_belacoder.sh`, `tools/pes_gaps.py`.
  - Gilt ab dem nächsten Start der Sendung (der Baustein wird beim Update neu gebaut).
  - Nicht geprüft: Hörprobe (nur Pegelsprünge gemessen), Stunden-Dauerlauf.

## 0.9.93 (Beta)
- Sicherheit (Meldung von romestylez, Issue #25):
  - **Anmeldesperre:** Der Fehlversuch wird jetzt vor der Prüfung gezählt. Vorher kamen bei vielen gleichzeitigen Anmeldungen alle an der Sperre vorbei (60 gleichzeitige Anfragen = 53 Prüfungen statt 5). Dazu laufen höchstens 2 Passwortprüfungen (je ein node-Prozess mit bcrypt) gleichzeitig; weitere bekommen sofort "bitte gleich noch einmal versuchen" (429) und zählen nicht als Fehlversuch. Ein gelungenes Anmelden wird nicht gezählt.
  - **HTTP-Server:** Verbindungen ohne Aktivität werden nach 15 s beendet, der Inhalt einer Anfrage darf insgesamt höchstens 15 s brauchen (vorher blieb "Content-Length: 4000, aber 1 Byte" unbegrenzt offen und die Oberfläche war nicht mehr erreichbar). Höchstens 64 Verbindungen gleichzeitig, davon 16 je Absender.
  - **Erreichbarkeit:** Die Oberfläche nimmt nur noch Anfragen aus Loopback, privaten Netzen, Link-Local, 100.64.0.0/10 (Tailscale) und IPv6-ULA an; öffentliche Quelladressen werden ohne Antwort getrennt (`--allow-public` hebt das auf). Der Tailscale-Proxy (Serve/Funnel) kommt von Loopback und ist nicht betroffen.
  - **Nicht gelöst:** Die Oberfläche lauscht weiter unverschlüsselt (HTTP) auf allen Schnittstellen, auch in fremden WLANs, über die die Box sendet; dort gehen Passwort und Sitzung im Klartext. Das steht jetzt ehrlich in KONZEPT.md und README (nur über Tailscale/HTTPS benutzen). Ein Schalter "über fremde WLANs sperren" ist offen (Fremdnetz-Erkennung fehlt noch).
  - Tests: `tools/test_security.py` (12).

## 0.9.92 (Beta)
- Geändert: Eine Kamera im Modus "Nur Akkustand lesen" verbindet sich immer automatisch, auch nach einem Neustart des Bluetooth-Dienstes oder einem Update (vorher stand sie danach getrennt da und der Akku fehlte im Status). Das Häkchen "Automatisch verbinden" entfällt bei diesen Kameras. Eine bestehende Einstellung ohne automatisches Verbinden wird beim Laden nachgezogen.

## 0.9.91 (Beta)
- Behoben: **Abbruch der Sendung nach mehreren Ton-Wechseln** (Ton-Knopf in der Fußleiste, Tausch, Ton-Quelle in der Pipeline-Karte). Ursache: Beim Wechsel der Tonquelle schloss die Ausgangszeit des Tons nahtlos an das Ende der alten Quelle an. Zwischen dem letzten Ton-Puffer der alten und dem ersten der neuen Kamera vergehen aber 0,2 bis 0,7 s (DJI liefert den Ton stoßweise). Bei jedem Wechsel fiel der Ton so ein Stück hinter die Echtzeit zurück (gemessen: 7 s nach 19 Wechseln). Der Muxer gab das Bild dann nur noch stoßweise frei, die Bitrate brach ein, bis `belacoder` den Ausgang für stehend hielt und neu startete.
  - Jetzt führt der Umschalter für jeden Tonweg den "pünktlichen" Zeitabstand zur Echtzeit und hält ihn beim Wechsel ein. Was vom neuen Weg dadurch vor dem Ende des alten läge (verspätet ausgelieferte Puffer), wird verworfen; die Zeit läuft nie rückwärts. Gemessen mit dem echten `belacoder` und den echten Kamera-Streams der Box (ohne Netz, `tools/boxtest_switch_belacoder.sh`): vorher Stillstand nach 13 bis 22 Wechseln, jetzt 45 Wechsel in 6 Minuten ohne Stillstand (auch mit Bildtausch dazwischen); auf der laufenden Sendekette 36 Ton-Wechsel ohne Neustart. Die Tests der Ansicht (`boxtest_e2e_view.py`, Tausch und klassisch) laufen mit dem neuen Baustein durch.
  - Gilt ab dem nächsten Start der Sendung (der Baustein wird beim Update neu gebaut).
  - Hinweis: Sind die Verzögerungen der Kameras nicht aufeinander abgestimmt, springt der Ton beim Wechsel um diesen Unterschied (ein Stück fehlt oder ist kurz still). Die Verzögerungen sollten so stehen, dass die Quellen gleichzeitig ankommen (siehe Kamerakarte).
  - Nicht geprüft: lange Läufe über Stunden und Bild-Wechsel allein ohne Ton.

## 0.9.90 (Beta)
- Sprachen: Alle neuen Texte seit 0.9.85 sind jetzt in allen 14 Sprachen übersetzt (maschinell, nicht von Muttersprachlern geprüft). Der Test verlangt wieder vollständige Wörterbücher.

## 0.9.89 (Beta)
- Geändert: Im Notbetrieb (weniger Kameras als eingestellt) startet eine Änderung von Ton oder Bild-Einblendung die Sendung **nicht mehr neu** (vorher 5 bis 6 Sekunden Pause). Sie wird gespeichert und gilt, sobald alle Kameras zurück sind. Stumm schalten geht im Notbetrieb nicht (Hinweis).
- Neu: Das Journal des Dienstes `pipbox` zeigt jeden Ton-/Ansichtswechsel mit Uhrzeit ("Ansicht live umgestellt ..., bestätigt nach x s" oder "nicht bestätigt, Neustart folgt"). So lassen sich Abbrüche der Sendung einem Wechsel zuordnen.
- Hinweis: Abbrüche beim Ton-Wechsel an einer echten Box (5. Okt) sind nicht nachgestellt worden; der Umschalter lief mit den echten Streams und Verzögerungen in Tests ohne Stillstand.

## 0.9.88 (Beta)
- Korrigiert: Schaltet man bei einer DJI-Kamera "Nur Akkustand lesen" ein, verschwindet ihr bisheriger eigener Eintrag in der Kameraliste. Vorher stand die Kamera doppelt da (als "HDMI" und mit ihrem Modellnamen), weil der Akku nun bei "HDMI" erscheint.
- **Mit echter Action 5 geprüft** (5. Okt): Sie sendet per HDMI und liefert zugleich den Akkustand per Bluetooth; er erscheint bei der HDMI-Kamera. Offen: Verhalten über Stunden und wenn der Akku leer wird.

## 0.9.87 (Beta)
- Korrektur: In 0.9.86 lagen `web/index.html`, `web/i18n/en.json` und `tools/test_dji_service.py` kurzzeitig am falschen Ort auf GitHub. Wer 0.9.86 in dieser Zeit eingespielt hat, hat die Oberfläche ohne den Schalter "Nur Akkustand lesen". Dieses Update bringt die richtigen Dateien. Sonst keine Änderung.

## 0.9.86 (Beta)
- Neu: **Nur Akkustand lesen** (Karte "Kameras", DJI-Kamera, Schalter "Nur Akkustand lesen (Kamera sendet per HDMI)"). Für eine Kamera, die per HDMI sendet: Die Box koppelt sich nur per Bluetooth, liest den Akkustand und lässt WLAN und Stream in Ruhe. Der Akku erscheint dann bei der HDMI-Kamera im Status und in der Twitch-Warnung. Standard: aus.
  - Mit nachgestellter Kamera geprüft; mit der echten Action 5 siehe 0.9.88.
  - Der Modus ist nur im getrennten Zustand umschaltbar. Er gehört zur Kamera und wird mit der Einstellungssicherung gesichert.

## 0.9.85 (Beta)
- Neu: **HDMI-Kamera**. Der HDMI-Eingang der Box (z. B. eine Action 5 per USB-C-HDMI) wird als Kamera "HDMI" eingespeist und ist wie jede Kamera wählbar (Bildaufbau, Fußleiste, Notbetrieb). Neuer Abschnitt "HDMI- und USB-Kameras" unten in der Karte "Kameras": Schalter "Als Kamera senden", Bitrate, Bildrate, Ton (HDMI-Ton oder ohne).
  - Eigener Dienst `pipbox-hdmi` (root): startet bei Signal, bei Ausfall oder neuem Bildformat neu. Hardware-Kodierung (H.264), gemessen etwa 40 % eines Kerns (1080p60 auf 30 fps).
  - **Mit echter Action 5 nur als Probelauf geprüft (Befehlskette und der Dienst aus einem Temp-Ordner), nicht als eingerichteter Dienst und nicht über die Oberfläche.** Verzögerung nicht gemessen. USB-Kameras fehlen noch.
- Neu: **Akku-Warnung im Twitch-Chat** (Karte "Kameras", Bereich "DJI-Kameras"). Fällt der Akku einer DJI-Kamera unter die Schwelle (Standard 10 %), schreibt ein Bot in den Chat (Twitch-IRC wie NOALBS, Token mit "chat:edit"). Kanal und Bot-Konto getrennt, Nachricht mit {Kamera} und {Prozent}, "Nur während der Sendung", Test-Knopf. Der Token bleibt auf der Box (nicht in der Einstellungssicherung).
  - **Nicht gegen echtes Twitch geprüft** (nur lokaler Testserver). Nur Kameras mit Bluetooth melden den Akku.
- Geändert: Die Überschrift des Kastens im Status heißt jetzt kurz "Up- und Download" (kein Zeilenumbruch mehr am Handy).
- Tests: `tools/test_hdmi.py` (39), `tools/test_hdmi_service.py` (29), `tools/test_twitch.py` (110).

## 0.9.84 (Beta)
- Neu (Issue #24): **Sprachen**. Standard ist Englisch. Beim ersten Öffnen erscheint die Auswahl, später steht sie im Kopf und unten in der Karte "Language". Auch die Anmeldeseite.
  - 14 Sprachen: Englisch, Deutsch, Französisch, Spanisch, Portugiesisch (Brasilien), Italienisch, Niederländisch, Polnisch, Türkisch, Russisch, Chinesisch (vereinfacht), Japanisch, Koreanisch, Thai. Datum und Uhrzeit folgen der Sprache.
  - **Außer Deutsch maschinell übersetzt, nicht von Muttersprachlern geprüft.** Fehlt ein Text, gilt Englisch, dann Deutsch. Verbessern oder neue Sprache: `web/i18n/README.md`.
- Geändert: Die Anzeige "verbunden" oben rechts entfällt.
- Geändert (Handy): Der äußere Rand ist schmaler (8 statt 16 Pixel). Kopf- und Fußleiste sind so breit wie die Karten, die Fußleiste ist abgerundet. Die Schatten reichen nicht mehr zur Seite (im hellen Modus heller).
- Tests: `tools/test_i18n.py` (41), `tools/test_mobile.py` (43).

## 0.9.83 (Beta)
- Neu (Issue #24, erster Teil): **Hell und dunkel**. Knopf mit Sonne/Mond im Kopf, die Wahl bleibt im Browser gespeichert, Standard bleibt dunkel. Gilt auch für die Anmeldeseite.
- Geändert (Issue #23):
  - "Beim Start der Box automatisch live gehen" steht jetzt in der Karte "SRTLA: Server, Bitrate und Latenz" ganz oben.
  - Am Handy entfällt der Block "Nicht live". Hinweise, Fehler und "Noch nicht übernommen" bleiben sichtbar, wenn es welche gibt.
  - Am Rechner fehlen im Block "Live gehen", "Live beenden" und "Streammodus", sie stehen im Kopf. Der Live-Knopf im Kopf hat die Farbe des früheren Knopfs.
  - Am Handy öffnen Kopf und Fußleiste keine Meldungsfenster mehr (die Rückfragen der Fußleiste entfallen, Fehler erscheinen in der Fußleiste).
- Tests in `tools/test_mobile.py` (41).

## 0.9.82 (Beta)
- Geändert (Issue #21): Das "i" steht nicht mehr im zugeklappten Kartenkopf, sondern **erst in der aufgeklappten Karte** (eigene Zeile oben). Das Menü bleibt aufgeräumt.

## 0.9.81 (Beta)
- Neu (Issue #19): **Kamera deaktivieren** per Doppeltipp auf den Kamera-Knopf in der Fußleiste (nochmal doppelt tippen aktiviert sie wieder). Eine deaktivierte Kamera springt bei Ausfall des Hauptbildes **nicht als Ersatz** ein; als kleines Bild bleibt sie, solange sie sendet. Sie wird blass und gestrichelt gezeigt, nie grün. Das Hauptbild lässt sich nicht deaktivieren; macht man eine deaktivierte Kamera von Hand zum Hauptbild, ist sie wieder aktiv.
  - Sendet nur noch eine deaktivierte Kamera, wartet die Sendung, bis eine aktive wieder sendet (Meldung "Keine aktive Kamera sendet"). Ohne laufende Sendung wird nur gespeichert, während der Sendung gilt es sofort ohne Neustart.
  - Der kurze Tipp (aus-/einblenden) wartet jetzt 0,3 Sekunden, ob ein zweiter Tipp folgt.
  - **Nur mit Tests und der Demo geprüft, nicht mit echten Kameras.**
- Tests: `test_failover` (68), `test_view` (81), `test_mobile` (29).

## 0.9.80 (Beta)
- Neu (Issue #21): **Hilfstexte auf dem Handy hinter einem "i"** (Breite bis 620 Pixel). Das "i" steht hinter der Überschrift (oder dem Kartennamen), ein Klick zeigt den Text, noch ein Klick blendet ihn aus. Meldungen und Warnungen bleiben sichtbar, am Rechner ändert sich nichts.
- Geändert (Issue #22): Im Status stehen die Kästen jetzt in der Reihenfolge **Upload, Kameras, System**.
- Tests in `tools/test_mobile.py` (27).

## 0.9.79 (Beta)
- Neu (Issue #20): **Einstellungen sichern und einspielen** (Karte "Einstellungen sichern"), zum Beispiel nach dem Neu-Aufspielen der SD-Karte.
  - Dabei: Kameras, Bildaufbau, SRTLA-Server, automatischer Start, Namen der Sticks, DJI-Kameras (Einstellungen), Hotspots, gespeicherte WLAN-Netze. Nie dabei: Passwort der Oberfläche, SSH, Schlüssel. Nicht übertragbar: Bluetooth-Kopplung der DJI-Kameras, Unternehmens-WLANs.
  - "Passwörter mitnehmen" ist vorgewählt. Eine Datei mit Passwörtern ist **immer mit einem Passwort verschlüsselt** (AES-256, PBKDF2-HMAC-SHA256, HMAC-SHA256, nur Standardbibliothek; gegen FIPS 197, NIST SP 800-38A und `openssl` geprüft).
  - Einspielen: Teile wählen, jeder Teil wird streng geprüft, nur ohne Sendung. Der Stand davor wird gesichert und lässt sich zurückholen.
  - WLAN-Netze über den Root-Helfer (`export_wifi`, `import_wifi`). Beim Einspielen steht das Passwort kurz in der `nmcli`-Befehlszeile.
  - **Nicht auf einer echten Box geprüft:** Anlegen der WLAN-Profile, Einspielen der DJI-Kameras.
- Tests: `test_settings` (65), `test_wifi_transfer` (20), `test_settings_ui` (12).

## 0.9.78 (Beta)
- Neu (Issue #19): **Fußleiste am Handy mit Kamera- und Ton-Knöpfen.** Bei der Art "Bild in Bild" steht in der festen Fußleiste (Breite bis 620 Pixel) in **einer Reihe**: "Live"/"Stop" links, daneben ein Knopf je Kamera im Bild und der Ton-Knopf. Mit der Art "eine Kamera" bleibt es bei "Live"/"Stop" allein. Die Knöpfe sind wie in der BELABOX-Oberfläche von Bittersweet1987 angeordnet (nur die Größen, kein Code übernommen).
  - **Kamera-Knopf** (Name = erstes Wort des Kameranamens, "Osmo Action 4" wird "Osmo"; heißen zwei gleich, kommt das nächste Wort dazu): **kurzer Druck blendet das kleine Bild aus oder ein** (blaues Symbol mit Strich = ausgeblendet; das Hauptbild lässt sich nicht ausblenden), **langer Druck macht die Kamera zum Hauptbild** (blauer Rahmen = Hauptbild). Das Symbol ist grün, wenn die Kamera gerade sendet, sonst weiß. Die Knöpfe bleiben in der Reihenfolge der Kameraliste stehen und springen beim Tausch nicht. Beim Tausch wird das kleine Bild der bisherigen Hauptkamera sichtbar, auch wenn dort vorher ein ausgeblendetes Bild stand ("ausgeblendet" gehört zur Kamera).
  - **Ton-Knopf** ("Ton: Osmo"): **kurzer Druck wechselt zur nächsten Tonspur** (Haupt, Klein 1, Klein 2, Klein 3, dann wieder Haupt; nur die vorhandenen Bilder), **langer Druck schaltet die Tonspur stumm** (rotes durchgestrichenes Mikro) und wieder laut. Stumm gilt nur für die laufende Sendung, eine neue Sendung beginnt nie stumm.
  - **Ohne Neustart der Sendung:** Ausblenden und Einblenden, Stumm, und im "Tausch ohne Unterbrechung" auch der Tausch und die Tonspur. Alles andere (Tonspur und Tausch ohne diese Einstellung) braucht einen kurzen Neustart des Encoders (etwa 5 Sekunden ohne Bild); davor fragt die Fußleiste einmal nach. Ohne laufende Sendung wird nur gespeichert. Lange Druck-Zeit: gut eine halbe Sekunde, ein Doppeltippen zoomt nicht.
  - **Noch nicht:** Doppelklick "Kamera deaktivieren". Offene Frage an den Melder, was das über das Ausblenden hinaus tun soll.
- Geändert: **Ansicht im Betrieb eingeschaltet.** Die Sendekette enthält jetzt einen Lautstärke-Regler vor dem Opus-Encoder (nur wenn der installierte Bild-Baustein 0.9.72 oder neuer ist). Der Baustein wurde mit **zwei Prüfungen auf der Box** abgenommen: (1) mit Testbildern und Testton (`tools/boxtest_view*.py`, schon seit 0.9.72), (2) neu mit der **echten Sendekette** (`tools/boxtest_e2e_view.py`): Pipeline-Text aus `server.py`, vier RTMP-Quellen am nginx der Box (Vollfarben, vier Tonpegel, H.264 und AAC), Hardware-Decoder und -Encoder, echter Baustein; Ausgabe in eine Datei, danach dekodiert und gemessen. **Mit dem Tausch ohne Unterbrechung (4 Kameras) und ohne:** Ausblenden einzelner und aller kleinen Bilder, Tonspur Haupt/Klein 1/2/3 (Pegel auf 1 dB genau), stumm (Stille) und wieder laut, Tausch mit Klein 1 samt Ton, Zurücktausch; **alle 62 Prüfungen bestanden** (38 mit, 24 ohne Tausch ohne Unterbrechung), die Sendekette lief durch alle Änderungen weiter, keine Fehlermeldung, der Baustein bestätigte jede Änderung nach 0,05 bis 0,15 s. **Nicht geprüft: echte Kameras** (andere Encoder, Profile, Tonformat) und lange Laufzeit.
- Tests: Fußleiste (Daten, Reihenfolge, Hauptbild, Ausblenden, Tonspur-Reihenfolge, Tausch blendet ein, Endpunkte), die Seitenskripte laufen in einer Attrappe der Seite (JavaScriptCore): Beschriftung, Zustände, keine Einschleusung über Kameranamen, Druckdauer, Anfragen, Rückfragen nur bei Unterbrechung.

## 0.9.77 (Beta)
- Geändert (Issue #18, Rückmeldung): **"SSH ausschalten" ist jetzt rot** (wie "Stop" bei der Sendung). Das Einschalten bleibt gelb.
- Geändert (Issue #8, die technischen Angaben des Melders ausgewertet): Beim TP-Link Archer T2U Nano (Treiber `rtl88XXau`, `wlan1`) zeigten die Angaben: Die Karte ist von NetworkManager verwaltet, Firmware in Ordnung, Funk an, kann Zugangspunkt sowie 2,4 und 5 GHz, **aber NetworkManager kennt 15 Funkstationen insgesamt und 0 auf `wlan1`** (Zustand "disconnected", Grund "Device disconnected by user or client"). Die Karte sieht also selbst nichts; die Anzeige ist nicht schuld. Zufällige MAC-Adressen beim Suchen sind auf der Box schon abgeschaltet (`wifi.scan-rand-mac-address=no`). Damit ich die Ursache weiter eingrenze:
  - **Suchlauf wie in der Original-Oberfläche:** Zuerst für alle Karten (`nmcli device wifi rescan`, so macht es das Original), danach gezielt für diese Karte. Vorher nur gezielt.
  - **Technische Angaben ergänzt:** Was NetworkManager auf die beiden Suchlauf-Anforderungen geantwortet hat (ein Fehler wie "Scanning not allowed" fiel bisher unter den Tisch), die WLAN-Einstellungen von NetworkManager (`wifi.scan-rand-mac-address`, `wifi.powersave`) und die letzten Meldungen von NetworkManager und wpa_supplicant zu dieser Karte (Namen in Hochkommas und IP-Adressen gekürzt). Dieselben Einstellungen stehen im Abschnitt "WLAN-Karten" des Protokoll-Downloads.
- Tests: Suchlauf fragt zuerst alle Karten und dann diese Karte und merkt die Antworten; "SSH ausschalten" ist rot.

## 0.9.76 (Beta)
- Behoben: **Eine neue Version war auf der Box bis zu sechs Stunden unsichtbar** (Rückmeldung: "Er findet auf der Box die 75 nicht"). Die Karte "Software-Update" fragt GitHub von selbst nur alle sechs Stunden und merkt sich die Antwort. Wer kurz vor einer neuen Version nachgefragt hatte, sah sie nicht, bis er "Nach Updates suchen" drückte. Das Archiv der Version war in Ordnung (auch gegen die Prüfung des Update-Helfers getestet), es lag nur am Zwischenspeicher der Box.
  - **Jetzt:** Wer die Seite öffnet oder die Karte "Software-Update" aufklappt, bekommt eine Antwort, die **nicht älter als fünf Minuten** ist (ältere werden erneuert). Die Abfrage im Hintergrund bleibt bei sechs Stunden, und während einer Übertragung wird weiterhin nicht nachgefragt (kein Mobilfunk-Verkehr ohne Grund). Der Knopf "Nach Updates suchen" erzwingt die Abfrage wie bisher.
  - **Hinweis:** Wer gerade eine ältere Version hat, sieht die neue Version erst nach einem Druck auf "Nach Updates suchen" oder nach dem Update auf 0.9.76 (die Box fragt dann beim Öffnen der Seite).
- Tests: `tools/test_ui_backend.py` (206): Hintergrundabfrage wartet weiter sechs Stunden, Öffnen erneuert eine Antwort älter als fünf Minuten, eine frische Antwort wird nicht erneuert, keine Abfrage während der Übertragung, Endpunkt und Seite.

## 0.9.75 (Beta)
- Geändert (Issue #8, "Deep Dive": TP-Link-Stick zeigt in der Original-Oberfläche Netze, bei uns nicht): **Die Netzsuche arbeitet jetzt wie die der Original-Oberfläche und liefert bei einem leeren Ergebnis technische Angaben.** Die Ursache für den TP-Link konnte ich ohne seinen Stick nicht beweisen; verglichen habe ich den Ablauf der Original-Oberfläche (`belaUI.js`, nur gelesen) mit unserem und drei Unterschiede gefunden, die ich angeglichen habe:
  - **Listen lesen wie das Original:** Das Original liest `nmcli device wifi list --rescan no` **ohne `ifname`** (alle Karten, zugeordnet nach der Spalte DEVICE). Wir lasen nur mit `ifname`. Jetzt wird die Liste **auf beiden Wegen gelesen und vereinigt**; Netze einer anderen Karte kommen nicht in die Liste.
  - **Länger warten, Ergebnisse laufend zeigen:** Das Original liest die Liste nach dem Suchlauf bis zu 20 Sekunden lang immer wieder nach (Ergebnisse trudeln ein, ein langsamer Stick mit beiden Bändern braucht dafür). Wir gaben nach 12 Sekunden auf. Jetzt wird **bis zu 25 Sekunden** gelesen (mindestens 8 Sekunden, auch wenn schon ältere Netze in der Liste stehen; Schluss, wenn 4 Sekunden lang nichts Neues kommt) und die Netze erscheinen **schon während der Suche**. Bleibt die Liste leer, wird der Suchlauf alle 10 Sekunden noch einmal angestoßen.
  - **Technische Angaben bei leerem Ergebnis:** Unter der leeren Liste steht ein aufklappbarer Block "Technische Angaben zur Karte": Zustand der Karte laut NetworkManager (verwaltet, Grund, Treiber und -version, fehlende Firmware), Fähigkeiten (WIFI-PROPERTIES), Funk (`nmcli radio`, `rfkill`), wie viele Funkstationen NetworkManager insgesamt und auf dieser Karte kennt (davon ohne Namen) und die letzten Meldungen des Treibers aus dem Kernel-Protokoll. MAC-Adressen sind gekürzt. Dieselben Angaben stehen im **Protokoll-Download** (neuer Abschnitt "WLAN-Karten", bereinigt).
  - **Bereinigung im Protokoll-Download:** MAC-Adressen in der Ausgabe von `nmcli -t` (dort mit `\:` maskiert) werden jetzt auch erkannt und ersetzt.
  - **Auf einer echten Box lesend geprüft:** Lesen der Liste und die technischen Angaben mit dem echten `nmcli` (Karte `wlan0` mit Treiber `rtl8821cu`); der Suchlauf selbst und der TP-Link-Stick sind weiterhin **nicht geprüft**.
- Tests: Suche liest auch die Liste ohne `ifname` und behält nur diese Karte, vorläufige Ergebnisse während der Suche, Mindestdauer, erneuter Suchlauf bei leerer Liste, technische Angaben nur bei leerem Ergebnis, MAC-Kürzung; Protokoll-Download mit Abschnitt "WLAN-Karten" und maskierten MAC-Adressen (`tools/test_ui_backend.py` 201 Tests, `tools/test_logs.py` 42).

## 0.9.74 (Beta)
- Geändert (Issue #19, Antwort des Melders): **Auch das Beenden der Sendung fragt nicht mehr nach.** Der Knopf "Stop" in der Fußleiste (Handy) und "● LIVE" in der Kopfleiste (Desktop) beenden die Sendung mit einem Klick; die Tooltips sagen nicht mehr "mit Rückfrage". (Der Start fragte schon seit 0.9.71 nicht mehr.) Ein versehentlicher Druck beendet die Sendung also sofort.
- Tests: `tools/test_mobile.py` und `tools/test_ui_backend.py` prüfen jetzt, dass es weder beim Start noch beim Beenden eine Rückfrage gibt.

## 0.9.73 (Beta)
- Geändert (Issue #16, Rückmeldung des Melders): **Der Hotspot wird jetzt je WLAN-Stick bedient.** Der große Abschnitt "WLAN-Hotspot" mit Kartenauswahl ist weg. Unter jeder WLAN-Karte steht stattdessen eine Zeile **"Hotspot-Modus"** mit einem Schalter **"Aus" / "An"** und, nur im Zustand "Aus", dem Knopf **"Einstellen"**.
  - **"Einstellen"** öffnet die Einstellungen (Name, Passwort, Band, Kanal, "Passwort erzeugen") direkt im Feld dieses Sticks; **"Speichern"** merkt sie, ohne den Hotspot zu starten (neue Aktion `hotspot_save` im Helfer). Läuft der Hotspot, geht das nicht (erst ausschalten, dann einstellen). Eingaben gehen nicht verloren, wenn sich die Anzeige im Hintergrund aktualisiert.
  - **Der Schalter "An"** startet den Hotspot mit den gespeicherten Einstellungen (die Oberfläche schickt nur die Karte, Name und Passwort kommen aus der gespeicherten Datei). Wurde der Stick noch nie eingestellt, öffnet der Schalter das Feld "Einstellen" und bittet um Name und Passwort, statt ohne Passwort zu starten. Ist die Karte mit einem WLAN verbunden, fragt die Oberfläche vorher nach (diese Verbindung endet), wie bisher.
  - **"Aus"** beendet den Hotspot (und schaltet den automatischen Start nach einem Neustart ab, wie bisher). **Neue Sticks stehen immer auf "Aus".** Karten, die keinen Zugangspunkt-Betrieb können, bekommen keinen Schalter. Läuft der Hotspot, gibt es **"Passwort anzeigen"** / **"Passwort ausblenden"** (groß, Klick kopiert). Die Karte des Kameranetzes zeigt nur "An" und das Passwort, sie lässt sich dort nicht ändern.
  - Der Hinweistext zum Hotspot steht jetzt als Tooltip am Wort "Hotspot-Modus".
- Tests: `tools/test_hotspot.py` auf 55 Tests (Speichern ohne Einschalten, gespeichertes Passwort bei leerem Feld, Ablehnung während der Hotspot läuft, Start mit gespeicherten Werten, Bestätigung bei verbundener Karte, Seite: Schalter je Stick, Einstellen nur im Zustand Aus, alter Abschnitt entfernt).

## 0.9.72 (Beta)
- Geändert (Issue #18, Rückmeldung des Melders): **Karte "Entwickler" überarbeitet.**
  - **Ein Knopf zum Umschalten:** "SSH einschalten" wird nach dem Klick an derselben Stelle zu "SSH ausschalten" (vorher gab es zwei Knöpfe, einer davon grau).
  - **Kürzer:** Der Erklärungstext und der Knopf "Original-Oberfläche öffnen" sind weg, ebenso die Zeile über dem Passwort. Die Statuszeile heißt jetzt "SSH ist ausgeschaltet · Benutzer: „user“" bzw. "SSH aktiv · Benutzer: „user“" (ohne den Hinweis zum Hochfahren und zum Passwort).
  - **Kein Popup mehr beim Einschalten.** Damit SSH trotzdem nie mit dem Auslieferungspasswort aufgeht, passiert das wie in der Original-Oberfläche: **Gibt es noch kein erzeugtes Passwort, erzeugt der Helfer beim ersten Einschalten eines** (zufällig, 20 Zeichen) und schaltet SSH erst dann ein; scheitert das, bleibt SSH aus. Hat die Original-Oberfläche oder IRL4YOU BOX schon eines erzeugt, bleibt es unberührt.
  - **Neu: "Passwort zurücksetzen"** erzeugt ein neues zufälliges Passwort für den SSH-Benutzer und zeigt es gleich an. Die Rückfrage dabei bleibt (das alte Passwort gilt danach nicht mehr).
  - **Neu: "Passwort ausblenden"**: Der Knopf "Passwort anzeigen" wird beim Anzeigen zu "Passwort ausblenden". Ein Klick auf das große Passwort kopiert es weiterhin; die Anzeige verschwindet auch beim Zuklappen der Karte.
  - **Technik und Grenzen:** Das Passwort setzt der Root-Helfer (`pipbox-ssh.py`, Stichwort `reset`) mit `chpasswd` **über stdin**, nie als Befehlsargument, und es steht nie in Protokoll oder Statusmeldung. Es liegt in `ssh-pass.json` (Benutzer pipbox, 0600, kein Verweis wird verfolgt) zusammen mit der Passwort-Zeile aus `/etc/shadow`, damit "von Hand geändert" erkannt wird; angezeigt wird es nur auf Knopfdruck. Die Datei der Original-Oberfläche wird dabei nicht verändert (die schreibt ihre Einstellungen selbst; ein Eingriff würde bei deren nächstem Speichern verloren gehen), ein von IRL4YOU BOX erzeugtes Passwort geht beim Anzeigen vor. **Weiter gilt:** Schlüssel, die Einstellungen von SSH und das Starten beim Hochfahren werden nicht angefasst; `install.sh` schaltet SSH nie selbst ein.
  - **Nicht geprüft:** Einschalten, Ausschalten und Zurücksetzen als Root-Dienst auf einer echten Box (`chpasswd` mit echtem `/etc/shadow`).
- Vorbereitung für Issue #19 (**noch nicht eingeschaltet**): Der Überlagerungs-Baustein (`gst/gstpbpip.c`, wird beim Update neu gebaut) kann jetzt kleine Bilder ein-/ausblenden, die Tonquelle wechseln und stumm schalten, während gesendet wird (Steuerdatei `main-view`, Rückmeldung in `/run/pipbox-send/view-state`). Auf der Box mit Testbildern und Testton geprüft (33 Schritte, auch mit Tausch); mit echten Kameras noch nicht. Die Sendekette nutzt es noch nicht (`VIEW_ENABLED` aus), für dich ändert sich an der Sendung nichts.
- Tests: `tools/test_ssh.py` auf 46 Tests erweitert (Zurücksetzen, erstes Einschalten, kein Passwort in Argumenten und Meldungen, Datei ohne Verweise, Passwort eines anderen Benutzers, Karte nach den Wünschen des Issues), neue Datei `tools/test_view.py` (41 Tests, Vorbereitung #19) und die Box-Prüfungen `tools/boxtest_view.py`, `tools/boxtest_view_swap.py`.

## 0.9.71 (Beta)
- Neu (Issue #19, erster Teil): **Mobile Ansicht (Handybreite bis 620 Pixel).**
  - **Feste Fußleiste mit einem großen Knopf "Live" / "Stop"** (grün, rot beim Senden, gelb "…" beim Verbinden). Der Knopf "Live gehen" / "● LIVE" ist auf dem Handy aus der Kopfleiste in die Fußleiste gewandert; auf dem Desktop bleibt alles wie es war. Die Fußleiste hat dieselben Bedingungen wie bisher (nur wenn die Sendung starten kann) und lässt unten Platz für die Navigationsleiste des Handys.
  - **Kopfleiste in einer Zeile** statt zwei (Name, Karten auf-/zuklappen, Streammodus, Verbindungsanzeige; die gelben Update-Hinweise sind auf dem Handy nur noch der Punkt, der Text steht im Tooltip).
  - **Keine Rückfrage mehr beim Start der Sendung** (mobile und normale Ansicht): "Jetzt LIVE senden? Ziel: …" entfällt. **Beim Beenden bleibt die Rückfrage**, damit ein versehentlicher Druck auf den großen Knopf die Sendung nicht abbricht; sag Bescheid, wenn sie auch dort weg soll.
  - **Noch nicht umgesetzt aus #19:** Ton-Knopf in der Fußleiste (Klick = nächste Tonspur, langer Klick = stumm), Kamera-Knöpfe ("Haupt", "Klein 1" bis "Klein 3") mit blauem Rahmen für das Hauptbild, langem Klick zum Tauschen und Ein-/Ausblenden. Das braucht Erweiterungen am Bild-Baustein, damit Ton und Sichtbarkeit **während der Sendung ohne Neustart** umgeschaltet werden können (heute geht das nur über einen Neustart der Sendung, ausgenommen der Tausch des Hauptbildes), und ein paar Rückfragen zur Bedeutung der Knöpfe (siehe Kommentar im Issue).
- Tests: neue Datei `tools/test_mobile.py` (5 Tests): Fußleiste nur auf dem Handy, Kopfleiste einzeilig, Beschriftung "Live"/"Stop" folgt dem Zustand, gleicher Schalter für beide Knöpfe, keine Rückfrage beim Start, aber beim Beenden.

## 0.9.70 (Beta)
- Neu: **Ein Klick auf ein angezeigtes Passwort kopiert es in die Zwischenablage** (SSH-Passwort in der Karte "Entwickler" und Passwort des Hotspots). Als Rückmeldung steht kurz "✓ kopiert" hinter dem Passwort. Auch ohne HTTPS (Adresse im lokalen Netz, dort gibt es die Zwischenablage-Schnittstelle des Browsers nicht) geht es über den Ersatzweg mit Markieren und `execCommand`. Klappt das im Browser nicht, bleibt das Passwort markiert und es steht "markiert: bitte Strg/Cmd+C" dahinter. Der Hinweis über dem Passwort heißt jetzt "(ein Klick kopiert es)".
- Tests: Seite enthält Kopierfunktion, Klick-Behandlung und den Hinweis.

## 0.9.69 (Beta)
- Behoben: **Bei den GitHub-Versionen 0.9.67 und 0.9.68 war `web/index.html` beim Hochladen eine Fehlerseite ("Error response, Error code: 404") statt der Oberfläche.** Wer in diesem Zeitraum (4. Oktober 2026, 19:54 bis 20:08 Ortszeit, Commits `ba1f3e4` und `b0c0254`) mit "Software-Update" auf 0.9.67 oder 0.9.68 gegangen ist, sieht nur diese Fehlermeldung. Ursache war ein Fehler beim Hochladen (die letzte Datei der Liste wurde übersprungen und die Fehlerseite des Hilfsservers hochgeladen), nicht die Software selbst. Die richtige Datei liegt seit 20:08 Ortszeit auf GitHub (Commit `7cb5a10`) (Commit "Reparatur: web/index.html …"). **Reparatur einer betroffenen Box** (Anmeldung per SSH als root, ein Befehl; die Oberfläche liest die Seite bei jeder Anfrage neu, ein Neustart ist nicht nötig):
  `curl -fsSL https://raw.githubusercontent.com/IRL4YOU/irl4you-pip/main/web/index.html -o /opt/pipbox/web/index.html`
  Alternativ `install.sh` aus dem Archiv der aktuellen Version erneut ausführen.
- Neu: **Der Update-Helfer prüft das Archiv jetzt auch auf die Seiten der Oberfläche.** `web/index.html` und `web/login.html` müssen mit `<!doctype html` beginnen, "IRL4YOU" enthalten und `</html>` haben; sonst wird das Update abgelehnt ("Die Seite im Archiv ist beschädigt") und nichts eingespielt. Eine Fehlerseite wie oben kommt damit nicht mehr durch. (Gilt für Updates, die der neue Helfer macht, also ab dem Update auf 0.9.69.)
- Geändert: **Das SSH-Passwort in der Karte "Entwickler" und das Passwort des Hotspots werden groß angezeigt** (22 Pixel, Schreibmaschinenschrift, markierbar; im Streammodus weiter ausgeblendet).
- Tests: Archiv mit guten Seiten, Fehlerseite statt `index.html` oder `login.html`, leere oder fremde Seiten, die echten Seiten dieser Version (4 neue Tests).

## 0.9.68 (Beta)
- Neu (Issue #18): **Karte "Entwickler" (nach "Protokolle"): SSH-Zugang ein- und ausschalten.** Wie in der Original-Oberfläche der BELABOX, im Aussehen dieser Oberfläche: Zustand (an/aus, Benutzer, ob SSH beim Hochfahren startet), die Knöpfe **"SSH einschalten"** und **"SSH ausschalten"**, **"Passwort anzeigen"** und ein Verweis **"Original-Oberfläche öffnen"** (dieselbe Box, Port 80). Die Plakette in der Kopfzeile der Karte zeigt auch zugeklappt "SSH an" oder "SSH aus".
  - **Passwort:** Das SSH-Passwort erzeugt weiterhin die Original-Oberfläche (Bereich "Advanced / developer", Knopf "Reset"); hier wird es **auf Knopfdruck angezeigt** (aus deren Einstellungsdatei, nie in der Statusabfrage; im Streammodus ausgeblendet). Ein Root-Helfer prüft dabei, ob das Passwort danach von Hand geändert wurde (Vergleich der Passwort-Zeile in `/etc/shadow` mit der gemerkten, wie in der Original-Oberfläche); dann steht dabei, dass das angezeigte vermutlich nicht mehr gilt. Gibt es noch kein erzeugtes Passwort, fragt die Oberfläche vor dem Einschalten nach (Auslieferungswerte machen die Box im Netz angreifbar), und "Passwort anzeigen" verweist auf die Original-Oberfläche.
  - **Was der Schalter tut und was nicht:** Er führt nur `systemctl start ssh` oder `systemctl stop ssh` aus (Root-Helfer `pipbox-ssh.py` über `pipbox-ssh.path`, feste Stichwörter `start`, `stop`, `check`). Er ändert **keine Passwörter, Schlüssel und keine Einstellungen von SSH** und schaltet auch das Starten beim Hochfahren nicht um: Nach einem Neustart gilt wieder die Einstellung des Systems (steht in der Karte). `install.sh` schaltet SSH nie selbst ein.
  - **Lizenz:** Alles ist eigener Code; aus der Original-Oberfläche (AGPL-3.0) wird kein Quelltext verwendet, nur zwei Einstellungsdateien werden gelesen (siehe NOTICE.md).
  - **Nicht geprüft:** Einschalten und Ausschalten als Root-Dienst auf einer echten Box (die Einheiten kommen erst mit dem Update dorthin) und der Vergleich der Passwort-Zeile mit echten Daten.
- Tests: neue Datei `tools/test_ssh.py` (27 Tests): Helfer (nur feste Stichwörter, Verweis als Auslösedatei abgelehnt, fehlender Dienst, falscher Endzustand, keine Passwort-/Schlüssel-/Konfigurationsbefehle im Helfer, Prüfung des Passworts schreibt nichts), Server (Benutzername geprüft, Passwort nie in der Statusabfrage, Bestätigung ohne erzeugtes Passwort, Anfrage während einer Aktion), Endpunkte (Anmeldung nötig) und Seite (Reihenfolge nach "Protokolle", `install.sh` ohne SSH-Befehl).

## 0.9.67 (Beta)
- Neu (Issue #16): **WLAN-Hotspot der Box.** In der Karte "Verbindungen" gibt es unter den WLAN-Verbindungen den Abschnitt "WLAN-Hotspot (Stick als Zugangspunkt der Box)": ein WLAN-Stick wird zum eigenen WLAN der Box, zum Beispiel für DJI-Kameras oder ein Handy (wie in der Original-Oberfläche der BELABOX, hier mit NetworkManager umgesetzt).
  - **Einstellungen:** Karte, Name, Passwort (8 bis 63 Zeichen, WPA2/CCMP), Band (2,4 oder 5 GHz) und Kanal (automatisch oder fest; 5 GHz nur die vier Kanäle ohne Radarpflicht 36 bis 48). Der Knopf "Passwort erzeugen" trägt ein zufälliges Passwort ein, "Passwort anzeigen" zeigt Name und Passwort des gespeicherten Hotspots. Karten, die keinen Zugangspunkt oder kein 5 GHz können (NetworkManager meldet das), werden nicht angeboten.
  - **Ablauf:** "Hotspot starten" legt ein Profil `pipbox-hotspot-<Karte>` an und schaltet es ein (Adressvergabe und Weitergabe macht NetworkManager, "shared", Adresse meist 10.42.0.1). Ein laufender Hotspot startet nach einem Neustart der Box von selbst wieder; **"Hotspot beenden"** schaltet das ab, und die Karte verbindet sich wieder mit einem gespeicherten Netz. Ist die Karte gerade mit einem WLAN verbunden, fragt die Oberfläche vorher nach (diese Verbindung wird beendet; wer die Oberfläche über dieses WLAN geöffnet hat, erreicht sie danach über den Hotspot oder ein anderes Netz). Scheitert der Start, wird das Profil wieder entfernt und die Karte nimmt ihr vorheriges Netz wieder auf.
  - **Kameras:** Der Hotspot erscheint bei der Kamera unter "Verbindung der Kamera" als "WLAN-Hotspot"; **Name und Passwort übernimmt die Box selbst.** NetworkManager gibt das Passwort an den Kamera-Dienst nicht heraus, deshalb liest der Kamera-Dienst es aus `hotspot.json` (nur für das Profil dieser Box). Die Karte des Hotspots ist kein Netz zum Senden (er hat keinen Weg ins Internet): Wurde sie zum Senden verwendet, nimmt die Box sie beim Start aus der Liste; ist sie das einzige Netz zum Senden, startet der Hotspot nicht.
  - **Sicherheit:** Der Root-Helfer prüft alles streng (Karte, Name, Passwort, Band, Kanal, Fähigkeiten der Karte); die Karte des Kameranetzes wird nie verändert; während ein Hotspot läuft, sind Suchen, Verbinden und Trennen auf dieser Karte gesperrt; der Profilname ist als Netzname reserviert (erscheint nicht unter den gespeicherten Netzen). Das Passwort eines Hotspots ist zum Weitergeben an Kameras und Handys gedacht und liegt in `hotspot.json` (Benutzer pipbox, 0600, kein Verweis wird verfolgt); es steht nicht in der Statusabfrage, sondern wird nur auf Knopfdruck gesendet. Es wird kurz als Argument an `nmcli` übergeben (anders als die Passwörter von WLANs, mit denen sich die Box verbindet). Im Protokoll-Download sind Hotspot-Name und -Passwort bereinigt.
  - **Geprüft:** `nmcli`-Befehl auf der Box (NetworkManager 1.36) mit einem nicht eingeschalteten Wegwerf-Profil (alle Felder wurden angenommen und stimmen, danach gelöscht); Karte meldet Zugangspunkt, 2,4 und 5 GHz; Oberfläche in der Vorschau. **Nicht geprüft: ein echter Hotspot mit einem Stick, einer Kamera oder einem Handy** (auch nicht 5 GHz mit DJI-Kameras).
  - **Hinweis:** `dji_daemon.py` ändert sich; der Bluetooth-Dienst startet beim Update neu (dank 0.9.61 etwa 13 Sekunden, laufende Kameras bleiben verbunden).
- Geändert (Issue #15, Nachtrag): Auch die Überschriften in der Karte "Verbindungen" heißen jetzt so: "Verbindungen zum Senden (Upload)", "WLAN-Verbindungen (z. B. Handy-Hotspot, auch zum Senden nutzbar)" und im Status "Upload (Verbindungen zum Senden)".
- Tests: neue Datei `tools/test_hotspot.py` (39 Tests): Befehl und Werte des Helfers, Ablehnungen vor jedem Befehl, Ersetzen eines alten Profils, gespeichertes Passwort bei leerem Feld, Fehlschlag bei Anlegen und Einschalten ohne Passwort in der Meldung, Beenden, Kameranetz, gesperrte Aktionen auf der Karte eines laufenden Hotspots, reservierter Name, Schreiben ohne Verweisen zu folgen; Server (Anfragedatei, Bestätigung bei verbundener Karte, Sendewege-Liste, einziges Netz zum Senden, Passwort nur auf Anfrage, Vorschau); Kamera-Dienst liest das Passwort nur für das Profil dieser Box; Protokoll-Bereinigung; Überschriften.

## 0.9.66 (Beta)
- Neu: **Knopf "Protokolle herunterladen"** (Karte "Protokolle"). Die Box sammelt in etwa einer Sekunde eine einzige Textdatei für die Fehlersuche (zum Beispiel für ein GitHub-Issue) und der Browser lädt sie herunter (`irl4you-protokolle-JJJJMMTT-HHMM.txt`, Ortszeit des Geräts im Namen). Inhalt: Version, Betriebszeit, Zustand der Dienste, Kurzfassung der Einstellungen (ohne Zugangsdaten), Netzwerkkarten, USB-Geräte, Bluetooth, Zustandsprotokoll der letzten Stunde, die Journale der IRL4YOU-Dienste (Kameras, Senden, Oberfläche, WLAN, Bluetooth-Treiber, Updates, Fernzugriff), Warnungen des Systems und Kernelmeldungen zu USB, Bluetooth und WLAN. Die Statusmeldungen der DJI-Kameras (alle paar Sekunden) sind bis auf die letzten 15 weggelassen, gleiche aufeinanderfolgende Zeilen werden zusammengefasst; die Datei bleibt unter 2 MB. Alle Zeiten in der Datei sind UTC (die Oberfläche sagt dazu, wie viele Stunden das bei dir ausmacht).
  - **Bereinigt, bevor die Datei die Box verlässt:** Passwörter, Stream-ID, Servername, WLAN-Namen (gespeicherte und in der Umgebung gesehene), IP-Adressen (außer lokal 127.x), IPv6-Adressen, MAC-Adressen (der Herstellerteil bleibt, zum Beispiel `<MAC-1 AC:DE:48>`), Kamera-Schlüssel (die aus der MAC-Adresse abgeleiteten `dji-…`-Namen), Tailscale-Namen und -Adressen, E-Mail-Adressen und alles hinter `password=`, `token=`, `streamid=`, `psk=`, `Authorization:` und ähnlichen Stichwörtern. Gleiche Werte bekommen gleiche Nummern (`<IP-1>`), damit man sieht, was zusammengehört. Mit den echten Daten einer Box geprüft: weder Klartext-Zugangsdaten noch Adressen in der fertigen Datei. **Bitte vor dem Weitergeben trotzdem kurz durchsehen**: Namen, die du selbst vergeben hast (zum Beispiel die Kamera-Namen), bleiben stehen.
  - **Technik:** Die Oberfläche hat keine Root-Rechte und kann die Journale nicht lesen. Sie legt nur das Stichwort `collect` in eine Auslösedatei; ein Root-Helfer (`pipbox-logs.py`, über `pipbox-logs.path`, gleiches Muster wie beim Protokoll-Modus) sammelt, bereinigt und legt die Datei in `/run/pipbox-logs` (nur root darf dort schreiben, im Arbeitsspeicher). Der Helfer liest die Einstellungsdateien ohne Verweisen (Symlinks) zu folgen. Im Modus "Sparsam" fehlt die Zeit vor dem letzten Start der Box (die Oberfläche weist darauf hin).
- Geändert (Issue #15): **Die Karte "Netze zum Senden und WLAN" heißt jetzt "Verbindungen".**
- Tests: neue Datei `tools/test_logs.py` (40 Tests): Bereinigung mit Zeilenformaten, wie sie auf der Box vorkommen (Journal, Zustandsprotokoll, NetworkManager, SRT-Adresse mit `streamid`, Bluetooth, `dev_…`-Pfade, IPv6), nichts Brauchbares geht verloren (Versionen, Uhrzeiten, Gerätebaum-Namen, Nulladresse), gleiche Nummern für gleiche Werte, Zugangsdaten aus den Einstellungsdateien und aus `nmcli`, Helfer (Auslösedatei, unbekanntes Stichwort, Verweis als Auslösedatei, Fehler ohne Interna, Größenbegrenzung), Server (altes Ergebnis zählt nach neuer Anfrage nicht, Zeitüberschreitung, kaputte Statusdatei) und die Endpunkte (Anhang, Anmeldung nötig).

## 0.9.65 (Beta)
- Behoben: **Die Karte "Software-Update" zeigte nach einem Update "installiert 0.9.64" und "Neueste auf GitHub 0.9.63".** Die Box fragte die neueste Version über `raw.githubusercontent.com`; dort liegt eine neue Version bis zu fünf Minuten im Zwischenspeicher, und die Antwort blieb zusätzlich sechs Stunden gespeichert. Das Update selbst lädt immer den aktuellen Stand, deshalb passten Anzeige und Wirklichkeit nicht zusammen.
  - **Frischer Stand:** Die Box fragt jetzt zuerst die **API von GitHub** (immer der aktuelle Stand) und nimmt `raw.githubusercontent.com` nur als Ersatz, etwa wenn die Anfragegrenze der API erreicht ist.
  - **Nie älter als installiert:** Ist die Antwort trotzdem älter als die installierte Version, gilt die installierte Version als neueste (kein "neuer" Hinweis, keine Anzeige einer älteren "neuesten" Version), und nach etwa drei Minuten wird noch einmal gefragt, nicht erst nach sechs Stunden.
- Tests: Antwort älter als die installierte Version, erneute Nachfrage nach der kurzen Wartezeit, normale Antworten bleiben stundenlang gespeichert, API zuerst und Rückfall auf raw.

## 0.9.64 (Beta)
- Geändert (Issue #7): **Die Skalierung eines kleinen Bildes ist von 1 bis 100 % frei einstellbar** (vorher 15 bis 40 %). Das Feld heißt jetzt "Skalierung (%)" (nicht mehr "Größe (% der Breite)"); 100 % ist so groß wie das Hauptbild. Sehr große Bilder brauchen mehr Rechenleistung (Hinweis im Tooltip).
  - **Sehr kleine Bilder:** Der Hardware-Decoder der Box verkleinert nur bis etwa 1:16 (auf der Box gemessen: ab 120 Pixeln Breite geht es, darunter "No valid frames decoded"). Unter 128 Pixel Breite (etwa 6,7 %) verkleinert er deshalb auf 128 × 72 und eine Software-Stufe (`videoscale`) macht den Rest; bei dieser Größe kostet sie fast nichts. Mit dem echten Decoder und dem neuen Baustein geprüft (2 %-Bild mit Rahmen in der Ecke, 100 %-Bild füllt das Bild).
  - **Große Bilder:** Ein Bild, das mit vollem Rand nicht mehr ins Hauptbild passt, wurde bisher nicht gezeichnet. Jetzt schrumpft der Rand, und das Bild bleibt im Bild (100 % liegt deckungsgleich auf dem Hauptbild); die Vorschau macht dasselbe. **Der Überlagerungs-Baustein wird beim Update neu gebaut.**
- Tests: Skalierung 1 bis 100 (Speichern, Prüfung, beide Pipeline-Aufbauten, Software-Stufe unter 128 Pixel), Position großer Bilder in allen Ecken.

## 0.9.63 (Beta)
- Behoben (Issue #11): **Der Name des WLAN-Sticks kommt jetzt aus der Hardware-Datenbank des Systems, wie in der Original-Oberfläche der BELABOX.** Viele Sticks melden selbst nur ihre Funknorm ("802.11ac NIC"). Ist der gemeldete Name so eine Standardbezeichnung, fragt die Box die Datenbank des Systems (`systemd-hwdb`) nach der USB-Kennung und zeigt deren Namen: für einen TP-Link Archer T2U Nano (USB 2357:011e) "**TP-Link Archer T2U Nano**" statt "802.11ac NIC". Auf der Box mit der echten Datenbank geprüft. Kennt die Datenbank nur den Hersteller (zum Beispiel Realtek 0bda:c811), steht der Hersteller zur USB-Kennung vor der Bezeichnung (die Marke geht dem Chiphersteller aus den Stickdaten vor). Ein echter gemeldeter Name (zum Beispiel "ASUS USB-BT500") bleibt unverändert. Das gilt für WLAN-Sticks und Bluetooth-Sticks; ein eigener Name ("umbenennen") geht weiter vor.
- Tests: Name aus der Datenbank für Standardbezeichnungen (mit eckigen Klammern, nur Hersteller, ohne Datenbank), nur einmal gefragt je Kennung.

## 0.9.62 (Beta)
Rückmeldungen zu den GitHub-Issues #7 und #8.
- Geändert (Issue #7): **Der Deckkraft-Regler pro kleinem Bild entfällt.** Ein Bild ist sichtbar ("Bild einblenden") oder ausgeblendet. Der Rahmen behält seine eigene Deckkraft (10 bis 100 %). Eine in 0.9.59/0.9.60 gespeicherte Deckkraft wird nicht mehr angewendet (das Bild ist voll sichtbar).
- Geändert (Issue #7): **Die Größe wird pro kleinem Bild eingestellt** (15 bis 40 % der Bildbreite), in jedem Block "Kleines Bild 1 bis 3". Das gemeinsame Feld oben entfällt. Bei bestehenden Einstellungen haben zuerst alle Bilder die bisherige Größe. Jedes Bild wird vom Hardware-Decoder auf genau seine Größe verkleinert. Beim Tausch ohne Unterbrechung folgt die Größe der Kamera (die Hauptkamera bekommt die Größe von Bild 1), nicht der Stelle.
- Geändert (Issue #7): **Die Eckenrundung gilt für das Bild selbst, mit oder ohne Rahmen.** Sie ist ein eigenes Feld ("Ecken") und nicht mehr Teil des Rahmens; Standard ist eckig (0). Mit Rahmen folgt dessen Innenkante der Rundung. Eine in 0.9.59/0.9.60 mit Rahmen eingestellte Rundung bleibt erhalten. **Der Überlagerungs-Baustein wird beim Update neu gebaut** (Rundung ohne Rahmen).
- Neu (Issue #7): **Nach "Gespeichert. Die Übertragung wird jetzt kurz neu gestartet." meldet die Oberfläche, wann die Übertragung wieder läuft** ("Die Übertragung läuft wieder (nach 9 Sekunden)."), und warnt, wenn sie nicht wieder anläuft.
- Behoben (Issue #8): **"Netze suchen" fand bei manchen WLAN-Sticks (zum Beispiel TP-Link, USB 2357:011e) nichts ("0 Netze gefunden")**, obwohl die Original-Oberfläche der BELABOX mit demselben Stick Netze findet. Der Suchlauf gab die Liste zurück, bevor er fertig war. Jetzt wird der Suchlauf angestoßen und gewartet (bis zu 12 Sekunden), bis die Liste nicht mehr leer ist, und noch einen Moment länger, damit sie vollständig wird. Bleibt sie leer, sagt die Meldung, dass man noch einmal suchen soll. Über der Liste steht, **von welcher Karte** sie stammt ("Gefundene Netze von wlan1"), und ein Hinweis, wenn sie von einer anderen Karte als der gewählten kommt. Nicht mit dem Stick des Melders geprüft, nur mit nachgebautem nmcli.
- Geändert (Issue #8): **Die Statusmeldung ("Läuft …", "Mit … verbunden") steht jetzt direkt unter den Knöpfen "Netze suchen", "Verbinden" und "Trennen"**, nicht mehr ganz unten unter den gespeicherten Netzen.
- Tests: Größen je Bild (Speichern, Prüfung, beide Pipeline-Aufbauten), Rundung ohne Rahmen im Zeichenkern und im Server, älterer Rundungswert im Rahmen, Suchlauf mit verzögerter Liste, mit Fehler beim Anstoßen und mit leerer Liste, Meldung nach dem Neustart.

## 0.9.61 (Beta)
- Behoben: **Nach einem Neustart des Bluetooth-Dienstes (zum Beispiel bei jedem Software-Update) antworteten die DJI-Kameras erst nach ein bis vier Minuten wieder, und man musste sie aus- und einschalten.** Ursache (auf der Box mit einem Mitschnitt der Funkdaten gemessen): Nach **jedem Trennen** der Bluetooth-Verbindung nimmt die Kamera etwa **50 Sekunden lang keine neue Verbindung an**. Auf Funkebene kommt die Verbindung zustande, die Kamera bricht sie nach 250 Millisekunden wieder ab ("Connection Failed to be Established"). Der Dienst hatte die Verbindungen bei jedem Neustart und bei jedem Aufräumen selbst getrennt oder abgerissen und damit diese Wartezeit ausgelöst; wiederholte Abbrüche beim Einrichten verlängerten sie auf mehrere Minuten.
  - **Verbindung weiterverwenden:** Hält BlueZ eine Kamera nach dem Neustart des Dienstes noch verbunden (das tut es, auch wenn der Dienst hart beendet wurde), nimmt der neue Dienst diese Verbindung einfach weiter, ohne zu trennen, zu suchen oder aufzuräumen. Klappt das nicht, räumt der nächste Versuch auf und verbindet wie bisher neu.
  - **Sauberes Beenden:** Der Dienst reagiert auf das Beenden durch systemd (Neustart, Update). Eine streamende Kamera bleibt dabei unberührt (kein Stopp des Streams, kein Trennen), der Dienst endet sofort. Eine Sitzung mitten im Einrichten hört nach dem laufenden Schritt auf und trennt, damit nichts halb eingerichtet zurückbleibt. `TimeoutStopSec=25` in der Dienstdatei.
  - **Kein Abreißen mitten im Einrichten:** "Trennen" oder "Neu verbinden", während die Kamera eingerichtet wird, beendet die Sitzung jetzt zwischen den Schritten (nach Kopplung, Vorbereiten, WLAN, Einstellungen) statt mitten in einer Nachricht.
  - **Ruhiger Hinweis statt Fehler:** Scheitert das Verbinden kurz (bis 90 Sekunden) nach dem Ende einer Verbindung, zeigt die Karte "Die Kamera meldet sich nach dem Trennen oft erst nach etwa einer Minute wieder. Der Dienst versucht es weiter." statt eines roten Fehlers. (Jedes Trennen beginnt die Wartezeit der Kamera von vorn: Tasten drücken verlängert sie.)
  **Gemessen auf der Box mit zwei Kameras (Action 4), Neustart des Dienstes während beide streamen, die Box sendet nicht:** vorher (Dienst trennt beim Beenden oder wird abgerissen) eine bis vier Minuten, im Versuch mit sauberem Trennen sogar 2 Minuten 43 Sekunden für eine der Kameras; **jetzt 13 bis 15 Sekunden**, ohne dass jemand eine Kamera anfasst. Nach einem harten Abbruch (kill -9) des Dienstes waren es 18 Sekunden.
  Hinweis: Das **Update auf diese Version** startet den Dienst noch mit dem alten Programm neu (es kennt das Weiterverwenden nicht): Dabei kann einmal die Wartezeit von etwa einer Minute auftreten. Ab dem nächsten Neustart gilt das Neue.
- Tests: Beenden eines streamenden und eines gerade einrichtenden Dienstes, Weiterverwenden einer von BlueZ gehaltenen Verbindung (ohne Suche und Aufräumen), Rückfall auf neues Verbinden, Abbruch zwischen den Einrichtungsschritten, ruhiger Hinweis nach dem Ende einer Verbindung, Lesen des Zustands aus BlueZ (`bluez_device_state`).

## 0.9.60 (Beta)
Meldungen aus den GitHub-Issues #11 und #12 (Namen von WLAN- und Bluetooth-Sticks).
- Geändert (Issue #11): **Der WLAN-Stick zeigt einen Gerätenamen statt der Standardbezeichnung.** Viele Sticks melden als Namen nur ihre Funknorm ("802.11ac NIC"). Ist der gemeldete Name so eine Standardbezeichnung ("802.11…", "WLAN", "Wireless", "Bluetooth Radio" und ähnlich), steht jetzt der Hersteller davor ("Realtek 802.11ac NIC"). Den **Handelsnamen** (zum Beispiel Logilink) kennt der Stick selbst meist nicht, darum gibt es hinter jedem Stick den Link **"umbenennen"**: Man vergibt einen eigenen Namen (bis 40 Zeichen, leer = Standardname). Er gilt überall, wo der Stick steht: in der WLAN-Übersicht, in der Auswahl "WLAN-Karte" und im Abschnitt "Bluetooth". Gespeichert wird je USB-Kennung (zwei gleiche Sticks teilen sich den Namen), bei einer eingebauten Karte je Schnittstelle oder Adresse.
- Geändert (Issue #12): **Bluetooth-Namen werden bereinigt.** "TP%Link UB500 Adapter" zeigte ein falsches Zeichen im Namen. Typografische Striche (zum Beispiel der geschützte Bindestrich), Steuerzeichen und doppelte Leerzeichen im gemeldeten Namen werden jetzt zum normalen Bindestrich bzw. entfernt, und Zeichen, die kein gültiges UTF-8 sind, bringen das Auslesen nicht mehr durcheinander. **Steht das Prozentzeichen wirklich so in den Daten des Sticks, ändert das nichts am gemeldeten Namen**: Dann hilft der neue Link "umbenennen". Zur Ursache bitte im Issue die Rohdaten schicken (`cat /sys/bus/usb/devices/*/product`).
- Tests: Standardnamen mit Hersteller, bereinigte Striche und Steuerzeichen, nicht lesbare Bytes, eigene Namen (Prüfung von Schlüssel und Name, Speichern, Löschen), Beschriftung der Bluetooth-Adapter und der WLAN-Karten.
- Hinweis zum Einspielen: Diese Version ändert `dji.py`; der Bluetooth-Dienst wird dabei einmal neu gestartet, die Kameras verbinden sich danach von selbst. Das Update läuft nicht während einer Sendung.

## 0.9.59 (Beta)
- Neu (Issue #7, Rest): **Deckkraft, Ein-/Ausblenden, Beschnitt und Rahmen pro kleinem Bild.** Im Bildaufbau hat jedes kleine Bild einen Block "Aussehen":
  - **Bild einblenden** (ein Schalter, die eingestellte Deckkraft bleibt erhalten) und **Deckkraft** 0 bis 100 %.
  - **Beschneiden:** Pixel links, rechts, oben und unten, bezogen auf ein Bild von 1920 x 1080 (links 400 ergibt 1520 x 1080). Es wird nur abgeschnitten, der Maßstab bleibt; Werte gerade, mindestens 32 Pixel bleiben stehen.
  - **Rahmen:** Dicke (1 bis 40 Pixel eines 1920 Pixel breiten Bildes), Farbe, Deckkraft des Rahmens (10 bis 100 %) und Eckenrundung (0 bis 60 Pixel). Der Rahmen liegt innen auf dem Bild (Größe und Position ändern sich nicht), die Rundung schneidet die Ecken ab und gehört zum Rahmen.
  Die Vorschau zeigt Rahmen, Rundung, Deckkraft und Beschnitt sofort; "Bildaufbau speichern" übernimmt sie (läuft eine Sendung, startet sie dabei kurz neu, wie bei anderen Änderungen). Die Einstellungen gelten je Stelle im Bild (kleines Bild 1 bis 3), auch beim Tausch ohne Unterbrechung.
  **Technik:** Der Überlagerungs-Baustein (`gst/gstpbpip.c`) bekommt die Eigenschaften `style1` bis `style3`; ein von GStreamer unabhängiger Zeichenkern (`PB_DRAW`) schneidet aus, mischt und zeichnet Rahmen und Rundung mit Ganzzahlrechnung (BT.709). **Das Hauptbild wird weiter nur dort gelesen, wo etwas durchscheinen muss** (Deckkraft unter 100 %, durchscheinender Rahmen, weiche Kante an gerundeten Ecken); beim Standardaussehen schreibt der Baustein wie bisher nur. Die Felder erscheinen erst, wenn der installierte Baustein sie kennt (er wird beim Software-Update neu gebaut); sonst bleiben sie ausgeblendet und die Sendekette ändert sich nicht.
  **Gemessen auf der Box** (echter Hardware-Decoder, 600 Bilder in 1080p, drei kleine Bilder, so schnell wie möglich, ohne Encoder): ohne Aussehen etwa 270 Bilder/s, Rahmen mit Rundung 230, Deckkraft 50 % bei allen drei Bildern 170, Deckkraft und Rahmen zusammen 148 Bilder/s. Bei 30 Bildern/s kostet die Deckkraft aller drei Bilder etwa 2 ms CPU-Zeit je Bild mehr (rund 7 % eines Kerns). Nicht gemessen: eine laufende Sendung mit Kameras und Encoder.
- Tests: 48 Tests für den Zeichenkern (Parser, Beschnitt, Rahmen, Deckkraft, Rundung, Speichergrenzen, Zufallsvergleich mit einer Pixel-für-Pixel-Rechnung, Lesezugriffe gezählt), dazu Bilder von der Box mit echtem GStreamer (Rahmen, Rundung, Deckkraft, Beschnitt, ausgeblendet). Die von `server.py` erzeugten Pipeline-Texte (normal und mit Tausch ohne Unterbrechung) werden vom neuen Baustein angenommen.

## 0.9.58 (Beta)
Meldungen aus den GitHub-Issues #7 bis #10.
- Behoben (Issue #8): **Ein gespeichertes WLAN ließ sich nicht erneut verbinden.** Wählte man ein gespeichertes Netz ohne Passwort, löschte die Box das gespeicherte Netz und versuchte es ohne Passwort ("Verbinden fehlgeschlagen: Password"); danach war es weg. Jetzt schaltet sie das gespeicherte Netz mit seinem gespeicherten Passwort auf der gewählten Karte ein und löscht nichts. Schlägt das fehl, bleibt das Netz gespeichert, und die Meldung sagt, dass man bei geändertem Passwort ein neues eingeben soll. Die gespeicherten Netze sind anklickbar (übernehmen den Namen), in der Liste der gefundenen Netze als "gespeichert" markiert, und das Passwortfeld sagt "leer = gespeichertes Passwort verwenden". Nach dem Verbinden wird die Anzeige der Karte noch zweimal aufgefrischt (nach 6 und 14 Sekunden), damit sie das neue Netz zeigt. Ein neu eingegebenes Passwort ersetzt das gespeicherte Netz wie bisher.
- Behoben (Issue #9): **Software-Update.** Die Knöpfe sperren sofort beim Klick (nichts lässt sich doppelt auslösen). **Nach einem erfolgreichen Update lädt die Seite von selbst neu** und bestätigt kurz "Update installiert: Die Box läuft jetzt mit Version ..."; ein Hinweis, die Seite selbst neu zu laden, ist nicht mehr nötig. Der Fehler war, dass das Neuladen an einen Zustand hing, der kurz nach dem Klick noch nicht gesetzt war. Die Ansicht der Änderungen ist gegliedert (Überschrift je Version, Aufzählungspunkte, fette Stichworte, Code, scrollbar bei viel Text) und zeigt **alle Versionen, die neuer sind als die installierte**, nicht nur die neueste.
- Geändert (Issue #10): **Kamerazeile.** In der Liste "RTMP-Kameras" steht die Verbindung jetzt als Spalte vor dem Signal: Name, Verbindung, Signal (fps, Mbit/s) und "Entfernen" in einer Zeile, das spart die Zeile darunter. Das Signal hat eine feste Mindestbreite mit gleich breiten Ziffern (kein Wackeln). Bei DJI-Kameras steht dort kurz "Hauptverbindung · DJI-Karte" (Erklärung im Tooltip).
- Geändert (Issue #7, erster Teil): **Die Vorschau im Bildaufbau ist eckig.** Hauptbild und kleine Bilder werden nicht mehr abgerundet dargestellt, ohne eigenen Rahmen und Schatten.
- Vorbereitet (Issue #7, Rest): Server und Oberfläche kennen jetzt Deckkraft, Ein-/Ausblenden, Beschnitt und Rahmen (Dicke, Farbe, Deckkraft, Eckenrundung) je kleinem Bild und prüfen und speichern sie. Die Felder erscheinen erst, wenn der installierte Überlagerungs-Baustein sie kann (er wird in einer der nächsten Versionen neu gebaut); bis dahin bleiben sie ausgeblendet, und die Sendekette ändert sich nicht. Der Pipeline-Text bleibt bei Standardaussehen unverändert.
- Tests: Wiederverbinden gespeicherter Netze mit nachgebautem nmcli (nichts wird gelöscht, Passwort nur über stdin), Änderungen aller neueren Versionen, Sperre und Neuladen der Update-Karte, Kamerazeile, Prüfungen und Pipeline-Text des Aussehens der kleinen Bilder.

## 0.9.57 (Beta)
Mehrere Meldungen aus den GitHub-Issues #4 bis #6 und eine Anzeigekorrektur im Status.
- Behoben (Issue #5): **Die Zwischenüberschrift „DJI-Kameras (Bluetooth)“ stand über der Hauptverbindung, die für alle RTMP-Kameras gilt** (Handy, Drohne, DJI-Kamera und so weiter), und ließ sie nach einer DJI-Einstellung aussehen. Im Menü „Kameras“ gibt es jetzt einen eigenen Abschnitt **„Hauptverbindung“** (Hinweistext, Auswahl und Adresszeile); darunter steht „DJI-Kameras (Bluetooth)“ nur noch mit dem, was wirklich zu den DJI-Kameras gehört (Adapter-Probleme, Kameras, Suche). Der Hinweis oben verweist jetzt auf „Hauptverbindung“.
- Geändert (Issue #4): **Beim Anlegen einer Kamera gibt es keine Auswahl „Weitere / Hauptbild / Bild-in-Bild“ mehr.** Welche Kamera Hauptbild oder Bild-in-Bild ist, wählt man im „Bildaufbau“; die Auswahl beim Anlegen passte dazu nicht mehr.
- Geändert (Issue #6): **Bluetooth-Sticks stehen im Menü „Netze zum Senden und WLAN“ als eigener Abschnitt „Bluetooth (für DJI-Kameras)“ nach der WLAN-Verbindung.** Jeder laufende Adapter ist mit Name, Hersteller und USB-Kennung gelistet („ASUS USB-BT500 (Realtek, 0b05:190e) · bereit“). Ein Stick, der steckt, aus dem der Kernel aber keinen Adapter macht (Treiber fehlt oder wird nicht unterstützt), steht dort ebenfalls, rot, mit dem Hinweis, was zu tun ist; so sieht man, dass er noch nicht kompatibel ist. Die Meldung „Bluetooth-Adapter: … Treiber für … ist eingerichtet“ in der DJI-Karte entfällt, wenn alles läuft; die DJI-Karte zeigt nur noch Probleme.
- Geändert (Issue #6): **Die Namen der WLAN-Sticks fehlten** in „WLAN-Verbindung“. Jetzt steht hinter dem Namen der Netzwerkkarte der Name des Sticks, wie er sich meldet (zum Beispiel „wlan0 · 802.11ac NIC (Realtek, 0bda:c811)“), bei einer eingebauten Karte der Treiber; auch in der Auswahl „WLAN-Karte“.
- Behoben: **Die Zahlen bei „Upload (Netze zum Senden)“ im Status wackelten**, weil sich ihre Breite beim Ändern der Werte verschob. Die Zeilen (Netz, senden, empfangen, Summe) stehen jetzt in einem gemeinsamen Raster mit festen Spalten und gleich breiten Ziffern, wie die Akku-Spalte bei den Kameras. Die Summenzeile heißt „Summe (2)“ (Zahl der Netze, Hinweistext „Summe über 2 Netze“), damit sie nicht mehr umbricht.
- Hinweis zum Einspielen: Diese Version ändert `dji.py` (Namen der Sticks); der Bluetooth-Dienst wird dabei einmal neu gestartet, die Kameras verbinden sich danach wieder von selbst. Das Update läuft nicht während einer Sendung.
- Tests: Namen aus /sys für Bluetooth-Adapter und WLAN-Karten (mit den Werten der echten Box), Reihenfolge der Abschnitte, Bluetooth-Abschnitt und entfallene Meldung, keine Rollenauswahl mehr, Raster der Upload-Zeilen.

## 0.9.56 (Beta)
- Geändert: **Akku-Spalte in "Status, Kameras" wackelt nicht mehr und kollidiert nicht mehr mit den Zahlen.** Akku und Datenrate haben jetzt feste Spaltenbreiten (Ziffern gleich breit), sodass sich nichts verschiebt, wenn das Ladesymbol erscheint, der Akkustand von 99 auf 100 % springt oder sich die Datenrate ändert. Der Akku sitzt damit ein Stück weiter links, mit Abstand zur Datenrate.
- Geändert: **Die Überschrift "Akku" steht in derselben Zeile wie "Kameras"**, in derselben blauen Schrift. Ohne Akkustand (zum Beispiel nur Handys) gibt es weiter nur "Kameras" und keine Akku-Spalte.
- Tests angepasst (feste Spaltenbreiten, gemeinsame Überschriftzeile).

## 0.9.55 (Beta)
- **Korrektur zu 0.9.54: Das Ladesymbol der Action 4 beruhte auf einer falschen Deutung.** In 0.9.54 galt Byte 2 der Statusnachricht als "Kabel steckt". Das war falsch: Die Bytes 1 bis 2 sind die **Akkuspannung** in mV (4400 am Kabel bei vollem Akku, etwa 4250 bis 4300 im Batteriebetrieb); Byte 2 wechselte nur, weil die Spannung die Stufengrenze 4352 mV überquerte. Bei niedrigerem Akku (unter 4096 mV) hätte 0.9.54 fälschlich ein Ladesymbol gezeigt.
  **Jetzt gilt der Strom aus dem Akku (Bytes 5 bis 8, vorzeichenbehaftet, in mA):** Aus dem Akku läuft die streamende Action 4 mit etwa -650 bis -1100 mA; steckt das Kabel, ist der Strom 0 bis -5 mA (die Kamera wird vom Kabel versorgt, der Akku wird nicht entladen), geladen würde positiv. Das Symbol 🔌 erscheint, wenn der Strom über -100 mA liegt. Das passt zu allen Wechseln am 4. Oktober (Kabel abgezogen und wieder angesteckt, mehrfach, mit Spannung und Strom gegen die Uhrzeit geprüft).
  Auch bei vollem oder fast vollem Akku und gestecktem Kabel zeigt es das Symbol: Die Kamera streamt dann aus dem Kabel und lädt nicht nach. Der Hinweis nennt es "am Ladekabel (lädt oder wird versorgt)". Weiter nur für die Action 4; bei anderen Modellen bleibt es unbekannt.
- Tests: Spannungs- und Stromwerte wie an der echten Kamera (Kabel steckt, abgezogen, angesteckt bei 94 %, Laden positiv), und ein Test gegen den Fehler von 0.9.54 (keine Spannung allein, auch nicht 3900 oder 4400 mV, ergibt "am Kabel").

## 0.9.54 (Beta)
- Neu (Issue #3, Rest): **Ladesymbol 🔌 für die Osmo Action 4 (in dieser Version mit falscher Erkennung, korrigiert in 0.9.55).** Die Statusnachricht der Kamera zeigt es: Byte 2 steht auf 0x11, solange das Ladekabel steckt, und auf 0x10, sobald es abgezogen ist. Das wurde auf einer echten Box gesehen: Beim Abziehen sprang das Byte, beim Wackeln am Kabel wechselte es mehrfach hin und her, und der Akkustand fiel danach von 100 auf 99 %. **Nur für die Action 4**, an der es beobachtet wurde; bei allen anderen Modellen bleibt "lädt" unbekannt (kein Symbol), nie geraten.
  Wer eine andere Kamera hat und das Laden zeigen möchte: Ladekabel abziehen und wieder anstecken und die Zeilen "Statusnachricht" aus dem Journal des DJI-Dienstes schicken.
- Geändert: **Akku im Status als eigene Spalte mit Überschrift.** In "Status, Kameras" steht der Akku jetzt in einer Spalte mit der kleinen Überschrift "Akku", mittig; Überschrift und Werte liegen übereinander. Die Spaltenbreiten richten sich nach dem Inhalt (ein gemeinsames Raster für Name, Akku und Datenrate): Ein langer Kameraname wird mit Auslassungspunkten gekürzt, statt Akku und Datenrate zu verschieben. Hat keine Kamera einen Akkustand (zum Beispiel nur Handys), gibt es die Spalte nicht.
- Geändert: **Das Journal des DJI-Dienstes ist übersichtlicher.** Statusnachrichten stehen nur noch drin, wenn sich ein Byte ändert, das nicht ständig schwankt (die Kamera schickt alle paar Sekunden eine neue Nachricht, in der einzelne Bytes dauernd wechseln). Vorher waren es einige hundert Zeilen pro halbe Stunde.
- Geändert: **Klarere Meldung nach dem Streamstart.** Meldet Bluetooth "getrennt", kommen aber weiter Statusnachrichten (Akku) von der Kamera, steht dort: "Der Stream läuft; die Steuerung per Bluetooth ist beendet, Statusmeldungen (Akku) kommen weiter" (so ist es bei der Action 4: BlueZ stellt die Verbindung selbst wieder her, bleak bemerkt das nicht). Der Akkustand bleibt dabei aktuell.
- Hinweis: Diese Version ändert den DJI-Dienst: Beim Einspielen werden die Kameras einmal getrennt und bauen ihren Stream neu auf. Nicht während einer Übertragung einspielen.
- Tests: Laden bei der Action 4 (Kabel an, ab, wieder an), unbekannt bei anderen Modellen, Journal ohne Dauerschwankungen (73 Tests für den DJI-Dienst).

## 0.9.53 (Beta)
Drei Wünsche aus den Issues auf GitHub (#1, #2, #3) und der Live-Knopf in der Kopfleiste.
- Neu (#1): **Updates werden nach dem Start der Box gesucht und angezeigt.** Die Box sucht nach dem Start von selbst (die GitHub-Abfrage nach 30 Sekunden, die Systemupdates nach 2 Minuten statt vorher 10) und **immer einmal nach jedem Start**, auch wenn die letzte Suche noch keine 6 Stunden her war. Öffnet jemand die Oberfläche (Anmeldung), wird gleich gesucht, ohne die zwei Minuten abzuwarten (einmal je Start, still).
  Fehlversuche (Router und Mobilfunk sind nach dem Start oft noch nicht da) werden in der ersten halben Stunde nach dem Start schneller wiederholt: die GitHub-Abfrage nach 3 Minuten, die Systemsuche nach 5 Minuten (später wie bisher 30 Minuten beziehungsweise 1 Stunde). Das Ergebnis zeigen die gelben Punkte "Oberfläche" und "System" in der Kopfleiste. Wie bisher nie während einer Übertragung (dann kein Mobilfunkverkehr für die Paketlisten); läuft nach dem Start sofort eine Sendung, wird erst danach gesucht.
- Geändert (#2): **Funnel ohne Zeitgrenze, ohne verwirrenden Hinweistext.** Die öffentliche Freigabe bleibt jetzt an, bis sie mit "Öffentliche Freigabe beenden" abgeschaltet wird, und besteht auch nach einem Neustart der Box fort (Tailscale behält sie). Die Grenze von 8 Stunden und der Zeitgeber, der sie beendete, sind weg (`pipbox-funnel-guard`; beim Einspielen wird er abgeschaltet und entfernt). Die rote Warnung und die Rückfrage vor dem Einschalten sagen das ausdrücklich.
  Die Zeile "Weitere Geräte in Ihrem Netz: …" (mit vielen "funnel-ingress-node") ist ganz entfernt, auch im Server.
- Neu (#3): **Akkustand der DJI-Kameras im Status unter "Kameras".** Neben dem Namen steht der Akku in Prozent: über 75 % grün, unter 25 % rot, sonst die Standardfarbe. Ein Steckersymbol zeigt, wenn die Kamera lädt (siehe unten). Auch in der DJI-Karte gelten die Farben.
  Verliert die Box die Bluetooth-Verbindung zur Kamera (die Action 4 trennt sie gleich nach dem Streamstart), bleibt der zuletzt gemeldete Wert stehen, wird blasser und zeigt im Hinweis, wie alt er ist ("Stand vor 25 Min.").
  **Laden:** Ob die Statusnachricht der Kamera "lädt" enthält, ist nicht bekannt, deshalb wird das Steckersymbol erst gezeigt, wenn der Dienst es sicher weiß; es wird nie geraten. Zur Klärung schreibt der Dienst jede geänderte Statusnachricht (außer dem Akkustand selbst) als Hexwert ins Journal ("Statusnachricht …"): Beim Anstecken oder Abziehen des Ladekabels sieht man, welches Byte sich ändert.
- Neu: **Live-Knopf in der Kopfleiste (immer sichtbar).** Er zeigt den Zustand der Sendung: "Live gehen" (nicht live), "● LIVE" (rot, es wird gesendet), "Verbinde …" und "Wird beendet…" (orange, gesperrt). Ein Klick startet die Sendung (mit derselben Rückfrage wie in der Live-Karte, mit Ziel) oder beendet sie (mit Rückfrage "Die Sendung jetzt beenden?"). Kann die Sendung nicht starten, ist der Knopf gesperrt und der Hinweis nennt die Gründe (zum Beispiel "Kein SRTLA-Server ausgewählt").
  Die graue Anzeige daneben hieß bisher "live" und meinte nur die Verbindung der Seite zur Box; das wurde mit der Sendung verwechselt und heißt jetzt "verbunden".
- Geändert: **Die Update-Hinweise stehen als Knöpfe in der Kopfleiste** ("Update Oberfläche" und "Update System", gelb umrandet, nur sichtbar, wenn es etwas gibt; ein Klick öffnet die zugehörige Karte). Vorher waren es kleine Punkte neben dem Titel. Auf dem Handy steht nur "Oberfläche" und "System".
- Geändert: **Einheitliche Kopfleiste.** Alle Elemente oben (Update-Hinweise, Zuklappen, Streammodus, Live-Knopf, Anzeige der Verbindung) haben jetzt dieselbe Form (abgerundetes Rechteck wie die übrigen Knöpfe), Höhe (30 Pixel) und Schrift; vorher mischten sich Rechtecke und Pillen.
- Geändert: **"Abmelden" steht nicht mehr in der Kopfleiste**, sondern in der Karte "Box ausschalten und abmelden" (neben "Herunterfahren" und "Neu starten"). Die Funktion bleibt, weil eine mit "Angemeldet bleiben" gemerkte Anmeldung 30 Tage gilt und man sich auf einem fremden Gerät abmelden können muss.
- Hinweis: Diese Version ändert den DJI-Dienst (Akkustand): Beim Einspielen werden die Kameras einmal getrennt und bauen ihren Stream neu auf. Nicht während einer Übertragung einspielen.
- Tests: Kopfleiste (Live-Knopf nutzt den bestehenden Weg, Abmelden in der Ausschalten-Karte), Suche nach dem Start (Wartezeit, immer einmal je Start, schnellere Wiederholung, Anmeldung), Funnel (keine Zeitgrenze, alter Zeitgeber wird entfernt und tut nichts, keine Geräteliste), Akkustand (bleibt nach Bluetooth-Verlust mit Alter, aus dem Zustand des Dienstes in die Kameraliste, Farbgrenzen in der Oberfläche geprüft).

## 0.9.52 (Beta)
- Neu: **Die Adresse jeder Kamera in der Liste "RTMP-Kameras" gilt für ihre eigene Verbindung**, nicht nur für die Hauptverbindung. Eine Kamera oder ein Handy, das über eine andere Verbindung der Box sendet (zum Beispiel einen zweiten Router, um die Last zu verteilen), zeigt die Adresse der Box in diesem Netz.
  - **Andere Kameras (zum Beispiel ein Handy):** In ihrer Zeile gibt es die Auswahl "Verbindung" mit allen Verbindungen der Box (mit Adresse, feste Zweitadressen wie bei der Hauptverbindung). Standard ist die Hauptverbindung. Ist die gewählte Verbindung gerade nicht da, gilt bis zu ihrer Rückkehr die Hauptverbindung; die Wahl bleibt gespeichert.
  - **DJI-Kameras:** Die Zeile zeigt die Verbindung aus der DJI-Karte der Kamera ("in der DJI-Karte gewählt"), ohne etwas doppelt einzustellen; hat die Kamera keine gewählt, steht dort die Hauptverbindung. Der Server verweigert, die Verbindung einer DJI-Kamera in der Liste zu setzen.
  - Die Auswahl "Netzwerk der Kamera" im Bereich "DJI-Kameras" heißt jetzt "Hauptverbindung" und gilt für alle Kameras ohne eigene Verbindung.
- Tests: Adresse je Verbindung (Standard, eigene Wahl, feste Zweitadresse, zurück zur Hauptverbindung, unbekannte und verschwundene Verbindung), DJI-Kamera nach ihrer Karte ("Manuell", nichts gewählt, Verbindung gerade nicht da), andere Kameras bleiben unberührt.
- Hinweis: Der DJI-Dienst ändert sich in dieser Version nicht; beim Einspielen werden die Kameras nicht neu verbunden.

## 0.9.51 (Beta)
Korrekturen zu 0.9.50 nach dem ersten Test mit echten Kameras auf der Box.
- Behoben: **"device not found" beim Verbinden** (Action 5 Pro, Action 6). BlueZ vergisst eine Kamera, sobald die Suche endet; die Vorlage beendete die Suche vor dem Verbinden. Jetzt läuft die Suche, bis die Verbindung steht, und endet erst dann. Dasselbe hatte schon die frühere Version gemessen.
- Behoben: **Der Stream wurde bei Bluetooth-Verlust neu gestartet.** Die Action 4 trennt Bluetooth gleich nach dem Streamstart (die frühere Version hat das nie geprüft, nur ob der Stream ankommt). Die Vorlage beendete dann die Sitzung und baute alles neu auf, mit "Stopp, Vorbereiten, Start": das Bild flackerte alle 25 Sekunden.
  Jetzt läuft ein ankommender Stream unverändert weiter; die Karte zeigt "Der Stream läuft, die Bluetooth-Verbindung ist getrennt". Endet der Stream, beginnt die Sitzung von vorn. Ohne Bluetooth kann "Trennen" den Stream nicht beenden (kein Stopp-Befehl möglich): Er läuft, bis die Kamera ausgeschaltet wird.
- Behoben: **Dauerschleife nach dem Bluetooth-Verlust** (Fehler beim ersten Entwurf dieser Korrektur): Der Dienst belegte einen ganzen Kern und blockierte sich selbst, Verbindungsaufbauten dauerten 11 bis 19 Sekunden statt etwa 3 Sekunden. Ein Test weist die Schleife nach.
- Geändert: **Wiederholung gestaffelt** (8, 8, 15, dann 30 Sekunden statt immer 8): Ständiges Suchen stört die schon stehenden Verbindungen.
- Neu: **Dieselbe Verbindung, dasselbe WLAN.** Wählt eine weitere Kamera eine Verbindung, an der schon eine andere Kamera ihr WLAN eingegeben hat, werden Name und Passwort angeboten (eingetragen und in ihrer Liste). Eine Kamera mit eigenem WLAN behält es; die Kameras bleiben sonst unabhängig voneinander ("Manuell" und andere Verbindungen bekommen nichts angeboten).
  Gemerkt wird, sobald Name und Passwort eingegeben sind und sobald die Kamera das WLAN angenommen hat.
- Neu: **Bildaufbau zeigt so viele Fenster wie Kameras da sind** (Hauptbild und höchstens drei kleine Bilder): Bei zwei Kameras zwei Fenster, bei drei Kameras drei, ab vier alle vier. Ein Fenster mit schon gewählter Kamera bleibt sichtbar.
- Geändert: Verständliche Meldungen statt "TimeoutError:" bei Zeitüberschreitung, "nicht mehr sichtbar", Abbruch beim Einrichten; die Warnung von `bleak` 3.x im Journal ist weg (der Adapter wird je nach `bleak`-Version angegeben); das Journal nennt die Dauer der Verbindungsschritte ("Zeiten: Verbinden und Dienste …").
- Bestätigt auf der Box: Eine Action 4 verbindet in etwa 3 Sekunden bis "Koppeln", das WLAN wird übergeben, der Stream läuft. **Noch nicht bestätigt:** Action 5 Pro und Action 6 mit der Korrektur der Suche. Tests: 68 für den DJI-Dienst (alle grün).

## 0.9.50 (Beta)
- Neu: **DJI-Anbindung neu aufgebaut: Verbindung je Kamera aus den vorhandenen Verbindungen der Box wählbar, schnellere Wiederholung, bessere Statusmeldungen.**
  Der Bluetooth-Dienst (`pipbox-dji`, `dji_daemon.py`) folgt jetzt dem Ablauf des DJI-Dienstes von Bittersweet1987 (Bibliothek `bleak` statt direkter BlueZ-Aufrufe), angepasst an dieses Projekt (siehe NOTICE.md).
  - **Verbindung je Kamera:** Jede DJI-Karte hat eine Auswahl "Verbindung der Kamera" mit allen aktiven Verbindungen der Box. WLAN-Hotspot und WLAN-Netze: Name und Passwort holt der Dienst selbst aus NetworkManager. Alle anderen Verbindungen (Ethernet, USB-Router, Modem): WLAN-Name und Passwort der Kamera einmal eintragen und speichern (mehrere gespeicherte Netze je Kamera wählbar); die Adresse der Box kommt aus der gewählten Verbindung. "Manuell" für alles von Hand.
    Solange die Kamera verbunden ist oder sendet, ist ihre Verbindung gesperrt. Kann der Dienst ein WLAN-Passwort nicht aus NetworkManager lesen, sagt er das und verlangt die Eingabe von Hand. Passwörter gehen nie an den Browser.
  - **Statusmeldungen:** Zustand je Kamera mit Schritt (zum Beispiel "Koppelt (3/6)"), Detailtext, Akkustand und, nach einem Fehler, dem Grund samt Sekunden bis zum nächsten Versuch. Während eine Kamera verbindet oder gesucht wird, fragt die Seite jede Sekunde nach (sonst alle 2 Sekunden bei offener Karte).
  - **Schneller:** Fehlversuche werden alle 8 Sekunden wiederholt (vorher 10, 20, 40, 60 Sekunden), die Suche beim Hinzufügen dauert 8 statt 30 Sekunden, es gibt keine festen Pausen mehr zwischen den Schritten.
  - **Eine Kamera nach der anderen verbindet** (danach laufen alle parallel): Gleichzeitiges Verbinden bricht auf dem Funkchip ab (`le-connection-abort-by-local`, auf der Box mit vier Kameras gemessen). Das Dokument zur Vorlage hatte diese Sperre nicht, sie bleibt hier erhalten.
  - **Bleibt wie bisher:** eigener Dienst getrennt von der Oberfläche (eine Kamera streamt nur, solange die Box die Bluetooth-Verbindung hält), Token für die lokale Schnittstelle (nur der Benutzer `pipbox` kann es lesen), Benutzer `pipbox` mit den Schutzeinstellungen des Dienstes, Rollenprofile (erste Kamera Hauptbild: 1080p, 30, 8 Mbit/s; weitere Bild-in-Bild: 720p, 30, 4 Mbit/s), Schlüssel `dji-xxxxxx` (kein Zusammenstoß mit anderen Kameras), Sperre für Action 2, PIN `mbln` (schon gekoppelte Kameras fragen nicht neu), Nachrichtenkodierung für Action 6 und Pocket 4 byte-genau wie bei Moblin.
  - **Übernahme:** Beim ersten Start des neuen Dienstes werden die Kameras der früheren Version samt Namen, Einstellungen, gespeichertem WLAN, gewähltem Kameranetz und "soll laufen" übernommen (`dji-cameras.json`, nur für den Dienst lesbar). Die alten Dateien bleiben liegen.
  - **Installation:** `install.sh` richtet `bleak` mit `pip` ein (für Ubuntu 22.04 gibt es kein Paket; braucht Internet). Gelingt das nicht, bricht die Installation ab, bevor etwas verändert wurde, und der Update-Helfer stellt die vorige Version wieder her.
    Beim Einspielen werden die Kameras einmal getrennt (der Dienst startet neu) und verbinden sich danach von selbst wieder: **nicht während einer Übertragung einspielen.**
  - **Oberfläche:** Neue Karte je Kamera (zuklappbar, der Zustand wird gemerkt), Suche mit "Hinzufügen". Die Auswahl der Rolle (Hauptbild, Bild-in-Bild, Weitere) in der Liste "RTMP-Kameras" ist entfallen: Sie steht schon im Bereich "Bildaufbau". Die Rolle beim Anlegen einer Kamera von Hand bleibt.
    Die Schnittstelle der Seite zum Server heißt jetzt `/api/dji/cmd` (nur die elf bekannten Befehle und Felder gehen durch); die alten Wege (`/api/dji/start`, `stop`, `wifi`, `settings`) sind weg.
  - **Nicht getestet** (nur mit echter Kamera und Box möglich): bleak und BlueZ auf der Box mit dem eingeschränkten Dienstbenutzer, das Lesen der WLAN-Zugangsdaten aus NetworkManager als dieser Benutzer, das Verhalten der echten Kameras mit diesem Ablauf, die Zeitersparnis. Getestet ist der gesamte Ablauf gegen eine nachgestellte Kamera (56 Tests) und die Kodierung Byte für Byte gegen die bisher an echten Kameras bewährte.

## 0.9.49 (Beta)
- Behoben: **Fehlmeldung "Der Stick … wurde erkannt, aber der Kernel hat keinen Bluetooth-Adapter daraus gemacht" bei einem Stick, der einwandfrei läuft** (zum Beispiel ASUS USB-BT500 mit laufendem Adapter). Ursache: Die Zuordnung zwischen Stick und Adapter ging über die Kennung, die BlueZ für den Adapter meldet. Das ist aber in der Regel nur die BlueZ-Standardkennung (`usb:v1D6Bp0246`, Linux Foundation), nie die des Sticks; so galt jeder Stick als "nicht erkannt".
  Jetzt wird die USB-Kennung des Sticks aus `/sys/class/bluetooth` gelesen (vom Adapter aufwärts bis zum USB-Gerät); ein eingebauter Adapter hat keins und gilt weiter als "eingebaut". Die Standardkennung wird nie mehr für den Stick gehalten. Die Warnung für einen Stick ohne Adapter (zum Beispiel UGREEN mit BARROT-Chip) bleibt.
  Der Fehler war seit 0.9.43 drin: Die Tests hatten die Kennung des Sticks in der BlueZ-Meldung angenommen. Sie bilden jetzt die echten Werte nach (BlueZ-Standardkennung, Adapter unter dem USB-Gerät). Gegen die echte Box geprüft (nur gelesen): ASUS-Stick ergibt keine Warnung.
- Tests: Kennung aus /sys, eingebauter Adapter, nicht vorhandener Adapter, Stick mit BlueZ-Standardkennung ohne Warnung, Stick ohne Adapter weiter mit Hinweis.

## 0.9.48 (Beta)
- Behoben: **Die gelben Punkte in der Kopfleiste ("Oberfläche" und "System") blieben dauerhaft sichtbar**, auch wenn es keine Updates gab. Ursache: Die Darstellung der Punkte (`display`) überstimmte das Attribut `hidden`, mit dem sie ausgeblendet werden. Jetzt gilt für die ganze Seite eine feste Regel `[hidden]{display:none}`.
  Dadurch verschwindet auch die Zeile "Adresse im privaten Netz" mit dem leeren Link in der Karte Fernzugriff, solange Tailscale keine Adresse bereitstellt (sie wurde bisher ebenfalls immer gezeigt).
- Geändert: **Beide Abfragen laufen alle 6 Stunden von selbst.** Die Suche nach Systemupdates (bisher einmal am Tag) und die Abfrage der neuen Oberflächen-Version bei GitHub: Beide Punkte stimmen so auch, wenn gerade niemand die Seite offen hat. Der Server fragt jetzt auch ohne geöffnete Seite bei GitHub nach.
  Ein Fehlversuch (kein Internet) wird früher wiederholt: die Systemsuche nach 1 Stunde, die GitHub-Abfrage nach 30 Minuten (vorher erst nach 6 Stunden). **Nie während einer Übertragung** (kein Mobilfunkverkehr dafür); nach dem Start der Box frühestens nach 10 Minuten (Systemsuche) beziehungsweise 90 Sekunden (GitHub). Die Suche lädt nur Paketlisten, installiert nichts.
- Test: Die Seite muss die feste Regel enthalten, die Punkte sind im Quelltext ausgeblendet. Geprüft im Browser mit der tatsächlichen Sichtbarkeit (nicht nur dem Attribut): ohne Updates beide weg, mit Updates sichtbar, bei Fehler weg.

## 0.9.47 (Beta)
- Neu: **Zweiter gelber Punkt in der Kopfleiste für die BELABOX-Systemupdates** (Ubuntu- und BELABOX-Pakete). Die Kopfleiste zeigt jetzt bis zu zwei Punkte hinter dem Titel: "Oberfläche" (neue Version von IRL4YOU BOX, seit 0.9.46) und "System" (Systemupdates liegen bereit).
  Beide sind **nur sichtbar, wenn etwas ansteht**, sonst aus. Beim Darüberfahren steht, was ansteht (bei den Systemupdates Zahl der Pakete, Stand der Suche, ob danach ein Neustart nötig ist, und dass das Einspielen während einer Übertragung nicht geht); ein Klick öffnet die zugehörige Karte. Auf schmalen Bildschirmen steht nur der Punkt, ohne Text.
  Der Punkt "System" erscheint nicht, solange ein Update läuft oder die letzte Aktion fehlgeschlagen ist.
- Neu: **Tägliche stille Suche nach Systemupdates.** Bisher suchte die Box nur auf Knopfdruck, der Punkt hätte also nur das Ergebnis der letzten Suche gezeigt. Jetzt fordert die Oberfläche höchstens **einmal am Tag** eine Suche an (frühestens 10 Minuten nach dem Start der Box, nach einer Suche ohne Ergebnis erst nach 6 Stunden wieder,
  **nie während einer Übertragung** und nie, während schon etwas läuft). Der Root-Helfer bekommt dafür das Stichwort `autocheck` (nur der Server legt es ab, über die Schnittstelle ist es nicht erreichbar): Er sucht wie "Nach Updates suchen", zeigt dabei aber nicht "läuft" und meldet keinen Fehler,
  wenn die Paketliste nicht erreichbar ist (kein Internet): Der bisherige Zustand der Karte bleibt dann unverändert. Es werden nur Paketlisten geladen, nichts installiert.
- Neu: **"Angemeldet bleiben" auf der Anmeldeseite**, wie in der BELABOX-Oberfläche. Mit Haken gilt die Anmeldung 30 Tage und **übersteht Updates und Neustarts der Oberfläche**: Man muss sich danach nicht neu anmelden. Ohne Haken bleibt es bei 12 Stunden und die Sitzung endet mit einem Neustart.
  Auf der Platte liegt nur ein SHA-256 des Sitzungsschlüssels (`sessions.json`, nur für den Dienst lesbar), nie der Schlüssel selbst. Ändert sich das Passwort (BELABOX-Passwort oder eigenes), enden alle gemerkten Sitzungen. "Abmelden" beendet auch eine gemerkte Sitzung.
- Neu: **Feste Kopfleiste.** Die Leiste mit Titel, Update-Punkten, Streammodus und Abmelden bleibt beim Scrollen oben stehen und verdeckt keine Inhalte (ein Sprung zu einer Karte, etwa über den gelben Punkt, landet unterhalb der Leiste). Auf dem Handy ist die Leiste zweizeilig und braucht etwa 84 px.
- Geändert: **Vorschau (`--demo`) ohne Setup-Code.** Das Demo-Passwort ist von Anfang an gesetzt und wird auf der Anmeldeseite vorausgefüllt, es genügt ein Klick auf "Anmelden". Das gilt nur mit `--demo` auf dem eigenen Rechner (Adresse 127.0.0.1) und nur im Speicher; auf einer BELABOX gilt unverändert das BELABOX-Passwort.
- Tests: Angemeldet bleiben (übersteht Neustart, normale Sitzung nicht, nur Hash und Rechte 0600 auf der Platte, Abmelden, Ablauf nach 30 Tagen, Passwortwechsel). Demo-Passwort (kein Setup-Code, nichts auf der Platte, ersetzt nie das BELABOX-Passwort, ohne `--demo` keins). Wann die Suche fällig ist (nie geprüft, nach einem Tag, kürzlich geprüft, Übertragung, Anforderung liegt noch, kurz nach dem Start, Pause nach Fehlversuch), Anforderungsdatei mit dem festen Stichwort, Helfer (Fehler lässt den alten Zustand, nichts während der Übertragung, Erfolg wie bei "Suchen").

## 0.9.46 (Beta)
- Neu: **Gelber Punkt in der Kopfleiste, wenn es ein Update für die Oberfläche gibt.** Er steht hinter dem Titel "IRL4YOU BOX" und erscheint, sobald auf GitHub eine neuere Version liegt (auch bei zugeklappter Karte "Software-Update"). Beim Darüberfahren steht die neue und die jetzige Version
  (und, dass das Einspielen während einer Übertragung nicht geht, falls gesendet wird); ein Klick öffnet die Karte "Software-Update". Der Punkt verschwindet, wenn die Box aktuell ist. Die Abfrage bei GitHub bleibt wie bisher: höchstens alle 6 Stunden und nie während einer Übertragung.
  Er meint nur das Update dieser Oberfläche, nicht die System-Updates der BELABOX.

## 0.9.45 (Beta)
- Neu: **Treiber für Realtek-Bluetooth-Sticks, die der Kernel 5.10 nicht kennt (TP-Link UB500 u. a.), richtet die Box beim Einstecken selbst ein.** Ohne ihn starten diese Sticks ohne Firmware, finden keine Kameras und wirken tot. Beim Einstecken (udev-Regel) oder beim Start (Zeitgeber nach 3 Minuten, danach alle 15 Minuten)
  prüft der neue Root-Helfer `pipbox-btdriver.py`, ob ein solcher Stick steckt; wenn ja, baut er aus den mitgelieferten, **unveränderten Kernelquellen** (`bluetooth-src/`, v5.10.160, GPL-2.0, SHA-256 geprüft) das Modul `btusb` neu, mit zusätzlichen Zeilen `BTUSB_REALTEK` für `2357:0604` (TP-Link UB500), `2550:8761`, `2c4e:0115` (Mercusys MA530), `0bda:8771`, `0bda:a725`, `2b89:8761` und
  `0b05:190e` (ASUS USB-BT500; löst nichts aus, bekommt aber die Firmware), spielt es nach `/lib/modules/<Kernel>/updates/btusb.ko` ein und lädt Bluetooth neu. Probebau auf der Orange Pi 5 Plus: 7 Sekunden, `vermagic`, Abhängigkeiten und Alias-Tabelle identisch mit dem Standardmodul.
- Sicherheitsnetz: nur für den Kernel 5.10.160; nichts aus dem Internet; **nie während einer Übertragung** (das Neuladen trennt Bluetooth, der Helfer wartet); nach dem Laden müssen ein Adapter da sein, das neue Modul wirklich laufen und die Firmware ohne Fehler laden, sonst wird alles zurückgerollt und für diese Kombination nicht noch einmal versucht (der Standardtreiber läuft weiter);
  das Standardmodul des Kernels wird nie überschrieben; `install.sh uninstall` entfernt das eingespielte Modul. Ohne passenden Stick tut der Helfer nichts (auf Boxen mit dem ASUS-Stick ändert sich nichts).
- Neu: Die Karte "DJI-Kameras (Bluetooth)" zeigt den Zustand des Treibers ("wird gebaut", "wartet auf das Ende der Übertragung", "eingerichtet", Fehler mit Grund).
- Weiter nicht unterstützt: UGREEN Bluetooth 5.4 und 6.0 (BARROT-Chip): Dafür müsste der Kern des Kernels (`hci_core`) geändert werden, das lässt sich so nicht nachladen; die Oberfläche sagt es im Klartext.
- Geändert: Lizenzhinweis für die mitgelieferten Kernelquellen in `NOTICE.md` und `bluetooth-src/README.md`.
- Tests: Quellen und Patch (nur zusätzliche Zeilen, idempotent), Erkennung, kompletter Ablauf mit nachgestellten Bausteinen (Erfolg, kein Adapter, Firmwarefehler, altes Modul läuft noch, Bau schlägt fehl, keine Schleife, Warten während der Übertragung), Bau, Rückbau, Verdrahtung (udev, Einheiten, Installer).
  **Noch nicht mit einem echten TP-Link-Stick ausprobiert**; der Nutzer mit den Sticks testet.

## 0.9.44 (Beta)
- Neu: **Knopf "Öffentlich im Internet freigeben (Funnel)" in der Karte "Fernzugriff"** (sichtbar, wenn die Box mit Tailscale verbunden ist). Damit erreicht auch jemand **ohne Tailscale-App** die Oberfläche. Das ist ausdrücklich eine **Ausnahme von der bisherigen Regel "nie öffentlich"** und deshalb vorsichtig gebaut:
  Aus ist der Standard, ein Klick braucht eine ausführliche Warnung und die Bestätigung (der Server verlangt zusätzlich das Merkmal `public`); solange Funnel an ist, steht eine rote Warnung mit der Uhrzeit des Endes in der Karte; **nach 8 Stunden beendet ein Zeitgeber die Freigabe von selbst**
  (`pipbox-funnel-guard.timer`, alle 5 Minuten und 2 Minuten nach dem Start; die Zeitgrenze liegt im RAM, nach einem Neustart gilt eine noch aktive Freigabe als abgelaufen und wird beendet); "Öffentliche Freigabe beenden" schaltet sofort ab, die Freigabe im privaten Netz bleibt.
  Der Root-Helfer bekommt die Stichworte `funnel_on` und `funnel_off` (weiter nur feste Stichworte, keine Eingaben der Oberfläche). Der Wächter ändert nur Funnel-Freigaben für die Oberfläche der Box, keine fremden. Verlangt Tailscale, Funnel für das Netz einmal zu erlauben, zeigt die Karte den Link dazu.
  Beim Beenden setzt Tailscale alle Freigaben zurück; die Box stellt nur die der Oberfläche im privaten Netz wieder her.
- Neu: Hinter dem Tailscale-Proxy (Serve und Funnel) zählt für die Sperre nach falschen Anmeldungen die Adresse des Absenders (letzter Eintrag von `X-Forwarded-For`, nur wenn die Anfrage vom Proxy auf der Box selbst kommt). Vorher hätte ein Angreifer über Funnel alle anderen mit ausgesperrt, weil für den Server alle von `127.0.0.1` kamen.
- Behoben: **Die Karte der System-Updates klappte von selbst zu**, sobald keine Updates (mehr) anstanden, auch beim Laden der Seite. Sie öffnet sich weiter von selbst, wenn etwas ansteht (Updates, Fehler, Neustart nötig), klappt aber nie mehr von selbst zu.
- Doku: `ANLEITUNG-Fernzugriff.md` beschreibt Funnel, seine Risiken und das automatische Ende; README und Sicherheitshinweise angepasst.
- Tests: Helfer (Zeitgrenze, Wächter, fremde Freigaben, Fehlermeldung "Funnel nicht erlaubt"), Server (Bestätigungspflicht, Demo), Absenderadresse hinter dem Proxy, Installer-Einheiten. Mit echtem Tailscale und echtem Funnel **noch nicht ausprobiert**; die Befehle (`tailscale funnel --bg --yes 8780`, `tailscale funnel reset`) stammen aus der Hilfe der installierten Version 1.102.4.

## 0.9.43 (Beta)
- Neu: **Die Oberfläche zeigt, welcher Bluetooth-Adapter für die DJI-Kameras läuft, und erkennt Sticks, aus denen der Kernel keinen Adapter macht.** Unter der Kartenüberschrift "DJI-Kameras (Bluetooth)" steht jetzt der laufende Adapter (USB-Kennung); steckt ein Stick, der keinen
  Adapter ergibt, steht dort der Grund, und die Suche meldet statt "Kein Bluetooth-Adapter gefunden" den Hinweis. Erkannt wird über `/sys` (Schnittstellenklasse Bluetooth oder Hersteller Barrot), nur lesend.
- Bluetooth-Sticks (Ergebnis einer Recherche, noch nicht mit den Sticks selbst geprüft): Der TP-Link UB500 hat denselben Chip wie der getestete ASUS USB-BT500 (Realtek RTL8761B/BUV) und sollte wie dieser über die allgemeine Bluetooth-Klasse laufen. Die UGREEN-Sticks "Bluetooth 5.4" und "6.0" (Modell CM748)
  enthalten einen **BARROT**-Chip (`33fa:0010`/`33fa:0012`), den der Kernel 5.10 der BELABOX nicht unterstützt: Der Chip bleibt bei der Einrichtung hängen, es entsteht kein Adapter. Laut Berichten ist das erst in Linux 6.18 (und den Langzeitzweigen 6.12.58, 6.6.117) behoben. Ein eigener Kernel-Treiber wird bewusst nicht
  mitgeliefert: Ein Fehler darin könnte die ganze Box abstürzen lassen, und der nötige Teil des Kernels (`hci_core`) lässt sich ohne Neubau des Kernels nicht ändern. Die README nennt die geprüften und die ungeeigneten Sticks.
- Tests: Erkennung aus einem nachgestellten `/sys`-Baum, Zuordnung Adapter und Stick, Hinweise.

## 0.9.42 (Beta)
- Behoben: **System-Updates scheiterten nach einem unterbrochenen Paketlauf** mit "E: dpkg was interrupted, you must manually run 'dpkg --configure -a' to correct the problem" (Meldung eines Nutzers; typisch nach Stromausfall, Neustart oder abgebrochenem Update mitten im Paketlauf).
  Der Update-Helfer erkennt jetzt die Reste eines unterbrochenen Laufs (Dateien in `/var/lib/dpkg/updates` oder halb eingerichtete Pakete laut `dpkg --audit`), schließt ihn vor dem Update mit `dpkg --configure -a` und `apt-get -f install` ab und macht dann mit dem Update weiter.
  Läuft gerade ein anderer Paketvorgang (zum Beispiel eine automatische Aktualisierung), fasst er nichts an und meldet das. Gelingt die Reparatur nicht, steht in der Oberfläche der Befehl für die Konsole statt der letzten apt-Zeilen; auch für "Could not get lock" gibt es jetzt eine verständliche Meldung.
  Das Software-Update dieses Projekts (Karte "Software-Update") war davon nie betroffen.
- Tests: Erkennung (Reste, halb eingerichtete Pakete, belegte Sperre), Reparaturablauf, Meldungen, `do_run` mit Reparatur und Abbruch.

## 0.9.41 (Beta)
- Geändert: **Auf einer frischen BELABOX gibt es keinen Setup-Code und kein zweites Passwort mehr.** Die Oberfläche benutzt das Passwort der BELABOX (belaUI). Hat die BELABOX noch keins, steht auf der Anmeldeseite, dass es zuerst in der BELABOX-Oberfläche festgelegt werden muss;
  die Seite wartet darauf und zeigt die Anmeldung von selbst, sobald das Passwort da ist. Die Datei `/var/lib/pipbox/setup-code` wird auf einer BELABOX nicht mehr angelegt (eine alte wird beim Start gelöscht). Ein eigenes Passwort, das eine frühere Version gesetzt hat, bleibt gültig.
  Nur ohne belaUI (Entwicklung, Demo) gilt weiter das eigene Passwort mit Setup-Code.
- Tests: Anmeldearten (frische BELABOX wartet ohne Code, Passwort der BELABOX, eigenes Passwort ohne belaUI, früheres eigenes Passwort bleibt gültig, alte Codedatei wird gelöscht).

## 0.9.40 (Beta)
- Behoben: **Nach einem Tausch ohne Unterbrechung startete die automatische Umschaltung den Encoder etwa 3 Sekunden später doch neu** (gefunden im ersten Test mit echten Kameras: Einbruch im Upload nach dem Wechsel der Kamera). Die Sendekette führte ihre Einstellung nach dem Tausch zwar nach,
  die Reihenfolge der genutzten Kameras (die Anordnung) aber nicht; die Automatik hielt die neue Reihenfolge für eine neue Anordnung. Jetzt folgt auch die Anordnung der neuen Reihenfolge, der Tausch bleibt ohne Neustart.
- Tests: Regressionstest, der nach dem Tausch die Schleife der Automatik laufen lässt und eine neue Anordnung ausschließt, und der danach einen echten Ausfall prüft.
- Neu: Das Einspielen räumt einmalig die Testquellen `tst-a` bis `tst-d` aus der Kameraliste (eine frühere Version hat sie automatisch als Kamera aufgenommen, weil sie bei Tests an die Box gesendet wurden). Der Dienst steht dabei still; andere Kameras bleiben unberührt, auch solche mit ähnlichem Schlüssel.
  Wer Testquellen an die Box sendet, nimmt Schlüssel mit `test-` am Anfang: Die werden gar nicht erst als Kamera aufgenommen.

## 0.9.39 (Beta)
- Neu (experimentell, standardmäßig aus): **Hauptbild tauschen ohne Unterbrechung.** Im Bildaufbau wählt "Hauptbild tauschen ohne Unterbrechung" die Tauschgruppe: *aus* (wie bisher: der Encoder startet neu, etwa 5 Sekunden ohne Bild), *Hauptbild und erstes kleines Bild* oder
  *alle Kameras*. Ist die Gruppe gewählt, bekommt jede ihrer Kameras zwei Zweige (groß für das Hauptbild, klein für das Bild-in-Bild), das ergibt bei vier Kameras im Bild 6 statt 4 Dekodierungen (bei *alle Kameras* 8). Ein neuer Umschalter im Baustein
  (`pbpipsel`) wählt das Hauptbild; der Encoder läuft weiter, der Strom zum Empfänger reißt nicht ab. Die Zeitstempel laufen beim Umschalten lückenlos weiter (der Encoder sieht keinen Sprung), der Ton wird im selben Schritt mit umgeschaltet, und der Encoder bekommt
  beim Schnitt einen vollständigen Bildanfang. Welche Kamera Hauptbild ist und welche Kameras an welcher Stelle kleiner erscheinen, schreibt der Umschalter in jedes Bild; `pbpipmix` liest es dort, Hauptbild und kleine Bilder wechseln also im selben Bild.
  Das ist ein harter Schnitt; eine Überblendung ist geplant.
- Wie der Tausch läuft: `POST /api/pipeline/swap` schreibt eine kleine Datei (`/var/lib/pipbox/main-select`, vier Zahlen), die `pbctl` alle 0,1 s liest; der Baustein meldet den eingestellten Zustand zurück (`/run/pipbox-send/swap-state`), erst dann gilt der Tausch
  als übernommen. Kommt keine Rückmeldung, sendet die neue Hauptkamera nicht, weicht die Anordnung vom Aufbau ab (Notbetrieb) oder ist eine der Kameras nicht in der Tauschgruppe, gilt wie bisher der Neustart. Die Sendekette führt ihre Einstellung für die automatische
  Umschaltung nach, damit ein späterer Kameraausfall das Hauptbild nicht auf den alten Stand zurücksetzt. Die Verzögerung gehört weiter zur Kamera (die Steuerdatei folgt der Reihenfolge beim Aufbau); der Plan steht im Status der Sendekette (`swap`).
- Baustein: Platz 3 für das kleine Bild der Hauptkamera, neues Element `pbpipsel`, `pbctl` liest die Umschaltdatei und stellt die Warteschlangen je Kamera (`cam0` bis `cam3`) um. Wird der Baustein beim Update neu gebaut, ändert sich ohne die neue Einstellung nichts.
- Gemessen mit Testbildern per RTMP auf der Box (kein echter Kameratest): Hauptbild und alle drei kleinen Bilder wechseln richtig, die Zeitstempel von Bild und Ton laufen durch (kein Sprung, keine Lücke), die Rückmeldung kommt in unter 0,7 s. Last des Prozesses: 4 Dekodierungen
  ohne Tausch 43 bis 51 % eines Kerns, 6 Dekodierungen 46 bis 48 %, 8 Dekodierungen 54 bis 55 %, Temperatur 36 bis 38 °C. **Noch nicht geprüft:** mit echten Kameras, mit der Übertragung zum Empfänger und über längere Zeit. Beide Kameras der Gruppe sollten dieselbe Auflösung senden.
- Tests: Pipeline-Text (6 und 8 Dekodierungen, Warteschlangen, Ton), Umschaltzeile, Reihenfolge der Verzögerungen, Übergabe an die laufende Sendekette (mit und ohne Rückmeldung), Nachführen der Einstellung, Anfangszustand.
- Die Auswahl im UI heißt "Hauptbild tauschen ohne Unterbrechung (experimentell)".

## 0.9.38 (Beta)
- Behoben: Im Kasten "System" waren der sichtbare Abstand von der Oberkante bis zur Überschrift (gemessen bis zur Oberkante der Buchstaben: 15 Pixel) und der Abstand vom unteren Balken bis zur Unterkante (11 Pixel) nicht gleich. Der Innenabstand der
  Kästen ist jetzt oben 8 und unten 12 Pixel; sichtbar sind es dadurch oben und unten gleich 13 Pixel.

## 0.9.37 (Beta)
- Geändert: Auch die gewählte Hauptkamera hat in der Auswahl des Hauptbilds den Punkt vor dem Namen (grün = sendet, rot = fehlt), mit einem dunklen Rand, damit er auf der hellen Füllung gut zu sehen ist.

## 0.9.36 (Beta)
- Geändert: Die Knöpfe für die Auswahl des Hauptbilds haben jetzt dieselbe Eckenrundung wie alle anderen Knöpfe und Eingabefelder (8 Pixel statt vollständig rund), dieselbe Rahmenfarbe wie die zweiten Knöpfe und für die gewählte Kamera dieselbe Füllfarbe wie
  die Hauptknöpfe (zum Beispiel "Live gehen"). Karten bleiben bei 14, Kästen in der Karte "Status" bei 10 Pixeln.

## 0.9.35 (Beta)
- Geändert: Die Auswahl des Hauptbilds sieht jetzt aus wie ein Schalter: Unter der Überschrift "Hauptbild" stehen die Kameras, die im Bild sind, als Knöpfe nebeneinander (feste Reihenfolge der Kameraliste). Die Kamera, die gerade das Hauptbild ist, ist
  hervorgehoben; ein Klick auf eine andere macht sie zum Hauptbild. Der Punkt vor dem Namen zeigt, ob die Kamera sendet. Das ersetzt die Zeile "Hauptbild tauschen mit:" aus 0.9.34.

## 0.9.34 (Beta)
- Geändert: **Hauptbild gegen eine frei gewählte Kamera tauschen.** In der Live-Karte steht (bei Bild-in-Bild) "Hauptbild tauschen mit:" und ein Knopf je Kamera, die gerade als kleines Bild im Bild ist (zum Beispiel "⇄ Action 6").
  Ein Klick tauscht das Hauptbild mit genau dieser Kamera; Kamera und Verzögerung bleiben beisammen, Ecke, Größe, Position und die Wahl des Tons bleiben am Platz. Der Knopf "Bilder tauschen" aus 0.9.33 (immer das erste kleine Bild) entfällt.
  `POST /api/pipeline/swap` nimmt dafür `{"with": "<Kamera-Schlüssel>"}`; ohne Angabe gilt das erste kleine Bild.
- Tests: Tausch mit dem zweiten und dritten kleinen Bild, Ablehnung einer Kamera, die nicht im Bild ist, und der Hauptkamera selbst.

## 0.9.33 (Beta)
- Neu: **Hauptbild und kleines Bild tauschen** (Knopf "⇄ Bilder tauschen" in der Live-Karte, sichtbar, wenn ein Bild-in-Bild mit mindestens einem kleinen Bild eingestellt ist). Der Tausch vertauscht das Hauptbild
  mit dem ersten kleinen Bild. Kamera und Verzögerung bleiben beisammen; Ecke, Größe, Position und die Wahl des Tons (Hauptbild oder kleines Bild) bleiben am Platz. Läuft die Sendung, startet der Encoder dafür neu und das
  Bild ist etwa 5 Sekunden unterbrochen (nach Rückfrage); sonst wird nur die Einstellung getauscht. Das ist die erste Stufe des Szenenwechsels; ein Tausch ohne Neustart mit Überblendung ist geplant (siehe README).
  Neu: `POST /api/pipeline/swap`.
- Tests: Tausch von Kamera und Verzögerung, doppelter Tausch, Ablehnung ohne kleines Bild.

## 0.9.32 (Beta)
- Geändert: Im Kasten "Upload" sitzt der Strich über der Zeile "Summe" jetzt direkt an der Zeile und braucht keinen eigenen Platz mehr. Die Summenzeile ist so hoch wie die anderen Zeilen und liegt auf der gleichen Höhe wie
  die Zeilen im Kasten "Kameras".

## 0.9.31 (Beta)
- Geändert: Abstände in der Karte "Status". Zwischen dem Kartenkopf und den Kästen sind es jetzt 10 statt 22 Pixel (eine leere Meldungsfläche belegt keinen Platz mehr), zwischen der Überschrift eines Kastens und seiner
  ersten Zeile 4 statt 8 Pixel. Im Kasten "System" ist der Abstand über der Überschrift und unter dem unteren Balken gleich (je 11 Pixel).

## 0.9.30 (Beta)
- Neu: **Ampel für die Sendewege** im Kasten "Upload" der Karte "Status". Der Punkt vor jedem Netz zeigt: **grün** = der Weg trägt Pakete, **gelb** = verbunden, aber in Reserve (Laufzeit zu hoch oder zu unruhig, der Sender nutzt ihn kaum),
  **rot** = nicht verbunden oder kein Netz (zum Beispiel ein ausgefallenes WLAN), **grau** = keine Sendung. Beim Darüberfahren steht die Laufzeit. Die Daten stammen aus der Datei, die der Sender ohnehin alle paar Sekunden schreibt;
  der Server liest sie nur, das kostet praktisch keine Rechenleistung.
- Tests: Zuordnung "genutzt/Reserve/aus", veraltete und fehlende Datei.

## 0.9.29 (Beta)
- Geändert: Im Kasten "System" der Karte "Status" ist der Abstand zwischen der Beschriftung und der großen Zahl (CPU und Arbeitsspeicher) kleiner. Auch die Zahlen sind etwas kleiner (24 statt 26 Pixel) und die Zeilen enger. Der Kasten ist dadurch rund 13 Prozent niedriger und passt besser zu den Kästen "Kameras" und "Upload". Die Zeilen der Kameras haben jetzt dieselben Abstände und dieselbe Schrift wie die Zeilen unter "Upload".

## 0.9.28 (Beta)
- Neu: Im Kasten "Kameras" der Karte "Status" steht hinter jeder Kamera die aktuelle Eingangsbitrate in Mbit/s (bei einer Kamera ohne Signal ein Strich). So fällt eine schwach sendende Kamera auf, bevor sie ausfällt.

## 0.9.27 (Beta)
- Neu: Die Karte "Status" zeigt jetzt die Kameras mit Ampel, nur Name und farbiger Punkt (grün = im Bild, gelb = sendet, aber noch nicht im Bild, rot = kein Signal, grau = unbekannt). Man sieht
  den Zustand der Kameras so, ohne die Karte "Kameras" zu öffnen. Beim Darüberfahren steht die Erklärung.

## 0.9.26 (Beta)
- Behoben: **Der Encoder starb beim Rauswurf einer Kamera durch das Signal SIGPIPE (Code -13) statt sich geordnet zu beenden.** Der Sendedienst ignoriert SIGPIPE, Python setzt es aber beim Start eines
  Kindprozesses auf "tödlich" zurück. Jedes beobachtete Code -13 folgte 1 s auf den Rauswurf einer Kamera durch den RTMP-Server (vermutlich schreibt librtmp beim Schließen noch einmal in den toten Socket); der Encoder
  meldete sich dabei nicht geordnet vom SRT-Server ab, und der Wiederanlauf verzögerte sich um rund 4 s. Jetzt bleibt SIGPIPE für belacoder ignoriert; er endet über den normalen Fehlerweg (Code 0).
- Behoben: **Die RTMP-Leerlaufgrenze von 15 s (0.9.18) ging bei einem Update des BELABOX-Pakets `belabox-rtmp-server` stillschweigend verloren.** Die Datei gehört dem Paket und ist keine dpkg-Konfigurationsdatei;
  ein Update setzt sie wieder auf 4 s. Neu: `install/pipbox-nginx-guard.sh` übernimmt die Änderung (aus `install.sh` herausgelöst), und ein apt-Haken (`/etc/apt/apt.conf.d/99pipbox-nginx`) ruft es nach jedem
  Paketlauf auf, auch nach Updates über die BELABOX-Oberfläche. nginx wird nur neu geladen, wenn gerade nicht gesendet wird (das Neuladen trennt alle Kameras); sonst gilt der Wert ab dem nächsten Start von nginx.
  `install.sh uninstall` entfernt den Haken und stellt die Datei wieder her.
- Neu im Protokoll: beim Ende des Encoders steht jetzt das Signal (`Code -13, Signal SIGPIPE`), die letzte erkannte Meldung mit dem Elementnamen (`... (rtmpsrc1)`; nie Adressen oder Schlüssel).
- Geändert: In der Karte "Status" steht die Meldung "Alles im grünen Bereich" nicht mehr, denn die Live-Karte zeigt sie schon. Gibt es Warnungen, erscheinen sie weiter in beiden Karten.
- Tests: `tools/test_send_hardening.py` (SIGPIPE-Verhalten mit echten Kindprozessen, Protokollzeilen, Schutzskript mit Attrappen für nginx, Einbindung in `install.sh`).

## 0.9.25 (Beta)
- Geändert: Die Karte "Status" zeigt CPU und Arbeitsspeicher jetzt in einem gemeinsamen Kasten "System". Die Liste der einzelnen CPU-Kerne mit ihren Taktfrequenzen entfällt; geblieben sind der Gesamtwert, der
  Hinweis "höchster Kern", die Temperatur und (falls vorhanden) die Lüfter-Ansteuerung. Das spart Platz. Die Messwerte je Kern liefert der Server weiterhin (`/api/metrics`).

## 0.9.24 (Beta)
- Neu: **Mindestanteil je Sendeweg** bei der Verteilung "alle" (Schalter "Alle Leitungen gleichzeitig nutzen, auch langsamere"). Bisher bekam der beste Weg fast alles (bei einem Test zu Hause 94 %, die
  beiden Mobilfunkwege 4 % und 2 %), und die schwächeren Wege waren nicht eingefahren, wenn der beste ausfiel. Jetzt bekommt jeder **geeignete** Weg (innerhalb des Laufzeitabstands, frische Messung,
  freies Fenster) mindestens 10 Prozent der Pakete. Das gilt in beide Richtungen: schwächerer Mobilfunk neben gutem DSL ebenso wie schwächeres Starlink neben gutem 5G. Ein Weg außerhalb des
  Laufzeitabstands oder mit vollem Fenster bekommt keinen Mindestanteil. Bei "beste" gilt nichts davon. Neu: `srtla/srtla_send-min-share.patch` (AGPL-3.0, zweiter Patch nach der Wegewahl),
  `install.sh` baut den Sender neu, wenn ein Patch neuer ist (schlägt der Bau fehl, bleibt der bisherige). Der Sendedienst setzt `SRTLA_MIN_SHARE_PCT=10` nur bei "alle", die Änderung der Verteilung
  gilt wie bisher nach dem nächsten "Live gehen".
- Tests: Prüfprogramm `srtla/test_min_share.c` (Aufteilung 80/10/10 bei 10 Prozent, 100 Prozent beim besten Weg ohne Mindestanteil, Weg außerhalb des Abstands und Weg mit vollem Fenster ohne Anteil),
  Zuordnung der Umgebungsvariable zur Verteilung "alle".

## 0.9.23 (Beta)
- Geändert: Die Kameraliste zeigt den Zustand nur noch über die Farbe des Punktes (grün, gelb, rot, grau), der Text "im Bild" / "wartet noch ..." ist weg. Beim Darüberfahren (am Handy: langer Tipp)
  erscheint die Erklärung als Hinweis.

## 0.9.22 (Beta)
- Geändert: Der Punkt vor jeder Kamera zeigt jetzt den Zustand auf einen Blick: **grün** = sendet und ist im Bild, **gelb** = sendet, ist aber noch nicht (oder nicht mehr) im Bild,
  **rot** = kein Signal, **grau** = Status unbekannt. Der Text dahinter ("im Bild", "wartet noch 40 s, dann im Bild") bleibt als Erklärung.

## 0.9.21 (Beta)
- Neu: **Die Kameraliste zeigt, ob eine Kamera im Bild ist.** Bisher hieß grün nur "sendet an den RTMP-Server". Fällt eine Kamera aus und kehrt zurück, nimmt
  die Box sie erst nach 60 s stabilem Signal wieder ins Bild auf (damit eine wackelige Kamera nicht dauernd den Encoder neu startet). Jetzt steht hinter jeder
  Kamera "im Bild", "wartet noch 40 s, dann im Bild" oder "nicht im Bild" (orange), solange gesendet wird und die automatische Umschaltung läuft. Die Sendekette meldet
  dafür die verbleibende Wartezeit je Kamera in `status.json` (`failover.wait`).
- Tests: Wartezeit der Umschaltung, Statusdatei, Zuordnung "im Bild/wartet/aus" je Kamera.

## 0.9.20 (Beta)
- Geändert: Der "Sendemodus" (Knopf im Kopf der Seite und in der Live-Karte, blendet Adressen, Namen und Protokolle aus) heißt jetzt "Streammodus". Die Einstellung im Browser bleibt erhalten.

## 0.9.19 (Beta)
- Behoben: **Ein Aussetzer einer kleinen Kamera beendete die ganze Sendung.** Der Stall-Wächter von belacoder las die Position der gesamten Pipeline
  (das Maximum aller Senken, auch der kleinen Kameras mit den rohen Zeitstempeln ihrer Kamera-Sitzung) und beendete den Encoder, wenn die älteste kleine
  Kamera 2 bis 4 s nichts lieferte, obwohl Hauptbild und Ausgang liefen (Meldung "Das Eingangsbild stockte"). Neu: `belacoder/belacoder-stall-output.patch`
  (GPL-3.0, wie der Regler-Patch). Es zählt nur noch der Ausgang des Encoders, und der Wächter schlägt erst nach rund 6 bis 8 s ohne Fortschritt an.
  Die kleine Kamera verschwindet bei einem Aussetzer nur kurz aus dem Bild. `install.sh` baut belacoder neu (nur wenn ein Patch neuer ist; schlägt der Bau fehl,
  bleibt die bisherige Fassung) und legt einmal eine Sicherung `belacoder.vor-stallpatch` an. Die Live-Karte meldet jetzt "Der Ausgang stockte", wenn der Ausgang
  selbst stand.
- Neu: Das Journal (`journalctl -u pipbox-send`) nennt jetzt den Grund, den belacoder vor einem Ende meldet (zum Beispiel "Der Ausgang stockte"), einmal je
  Ereignis und nur als fester Text (nie Adressen oder Stream-ID). Bisher stand dort nur "belacoder beendet (Code 0)".
- Tests: Meldungstexte des Stall-Wächters und Journal-Eintrag je Ereignis.

## 0.9.18 (Beta)
- Behoben: **Kurze Aussetzer einer Kamera warfen sie aus dem Bild und der Encoder startete mehrfach neu.** Der RTMP-Server der BELABOX entfernt eine
  Kamera, die 4 s lang nichts schickt (`drop_idle_publisher 4s`); DJI-Kameras setzen im WLAN gelegentlich 4 bis 10 s aus (gemessen: 15 Rauswürfe in
  rund 90 Minuten bei einer Kamera). Jedes Mal startete der Encoder zweimal vergeblich neu, bevor die Kamera aus dem Bild genommen wurde, rund 10 s ohne Bild.
  Neu: `install.sh` setzt die Grenze auf 15 s. Das ist die einzige Änderung an einer BELABOX-Datei (`99-belabox-rtmp.conf`); Sicherung als
  `99-belabox-rtmp.conf.vor-pipbox`, `install.sh uninstall` stellt sie wieder her. Beim ersten Einspielen lädt nginx neu und die Kameras
  verbinden sich kurz neu. Nach einem Update des BELABOX-Pakets `belabox-rtmp-server` kann die Datei wieder auf 4 s stehen; dann `install.sh` erneut ausführen.
  Fehlt eine Kamera wirklich, schaltet die Box nach dem Ende des Encoders sofort um (vorher erst nach 5 s und zwei Fehlstarts).
- Behoben: Mit nur einer sendenden Kamera blendete der Encoder seine Regelwerte (`b:`, `rtt:`, `bs:`) oben rechts ins Bild ein. Die Einblendung ist entfernt.
- Tests: Sofort-Umschaltung nach Encoder-Ende (fehlende Kamera, alle da, Statistik unlesbar, keine Kamera, Rückkehr nach 60 s).

## 0.9.17 (Beta)
- Doku: Hinweis zum Empfänger in README und Änderungsliste gekürzt. Keine Änderung am Programm.

## 0.9.16 (Beta)
- Geändert: Der Hilfetext zum Schalter "Automatisch live gehen" steht nicht mehr dauerhaft in der Live-Karte, sondern erscheint nach einem Tipp auf das kleine "?"
  neben dem Schalter. Die Zeile bleibt so schlank.

## 0.9.15 (Beta)
- Neu: **Automatisch live gehen nach dem Start der Box** (Schalter in der Live-Karte, standardmäßig aus). Einmal pro Start der Box wartet die Box, bis ein
  SRTLA-Server gewählt ist und mindestens eine Kamera sendet (dieselben Voraussetzungen wie "Live gehen"), und startet dann die Sendung. Wiederholt
  den Versuch alle 20 Sekunden und gibt nach 10 Minuten auf, mit Angabe des Grundes in der Live-Karte. "Live beenden" oder von Hand starten bricht die
  Automatik für diesen Start ab; ein Neustart der Oberfläche (zum Beispiel durch ein Update) startet die Sendung nicht noch einmal. Die Einstellung steht in
  `autostart.json` im Zustandsordner (Rechte 0600), der Zeitpunkt der letzten Automatik über die Boot-Kennung des Systems.
- Tests: neun Fälle für die Automatik (einmal pro Start, Warten auf Kamera, Aufgeben, Abbrechen, neuer Start, Einstellung speichern und prüfen).

## 0.9.14 (Beta)
- Behoben: **Bitrate bricht ein und bleibt unten hängen.** Zwei Ursachen, beide gefunden und mit Messungen belegt:
  1. **Regler im Encoder (belacoder):** Der Original-Regler senkt die Bitrate schon bei normalem Mobilfunk-Rauschen der RTT (15 bis 30 ms) um
     mindestens 100 kbit/s, erhöht aber nur bei fast tiefstem RTT und nur um 30 kbit/s plus 3 Prozent pro halbe Sekunde. Er fällt dadurch nach
     einer kurzen Überlast auf das Minimum und kommt nicht mehr hoch. Neu: `belacoder/` mit einem kleinen Patch (GPL-3.0, Upstream-Commit
     `ccce9ca`): 30 ms Mindestabstand bei der RTT-Schwelle, kleinere Senkungsschritte bei niedriger Bitrate, mehr Toleranz beim Erhöhen. Die
     Schnellbremse bei echtem Stau bleibt unverändert. `install.sh` baut ihn nach `/opt/pipbox/bin/belacoder`; die Sendekette nimmt ihn, wenn er
     da ist, sonst das Original aus dem BELABOX-Paket (schlägt der Bau fehl, ändert sich nichts).
  2. **Empfänger:** Auch die Einstellungen des SRT-Empfängers (Wartezeit bei Verlustmeldungen) beeinflussen die Bitrate; zu große Werte lassen die
     Bestätigungen ausbleiben, und der Encoder senkt die Bitrate.
- Doku: NOTICE (belacoder-Patch, GPL-3.0), README (Empfänger-Hinweis).

## 0.9.13 (Beta)
- Neu: **Protokolle in zwei Stufen** (Karte "Protokolle: Speicherkarte schonen", getrennter Root-Helfer `pipbox-logmode.py` mit fester
  Liste "sparsam" / "ausfuehrlich"). *Sparsam*: Journal nur im Arbeitsspeicher (`Storage=volatile`, 20 MB) und Zustandsprotokoll
  in `/run` (höchstens 1 MB): im Normalbetrieb schreibt das Paket kaum etwas auf die Speicherkarte, nach einem Absturz oder
  Stromausfall bleibt aber keine Spur. *Ausführlich*: wie bisher (Journal dauerhaft, höchstens 30 MB und 7 Tage;
  Zustandsprotokoll auf der Karte, alle 10 s). Neue Installationen starten mit "sparsam"; Boxen mit dem früheren
  dauerhaften Journal bleiben beim Update auf "ausführlich". Die Modus-Datei ist `/etc/pipbox/logmode`, die Journal-Einstellung
  heißt jetzt `pipbox-journal.conf` (vorher `pipbox-persistent.conf`). Die Deinstallation entfernt beides.
- Doku: Installationsanleitung mit `wget` im README (Herunterladen, Entpacken, `install.sh`, Anmeldung, Setup-Code).
- Tests erweitert (Modus-Wechsel, Helfer, Zustandsprotokoll folgt dem Modus).

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
