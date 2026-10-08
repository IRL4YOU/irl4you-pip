# Twitch-Chat in der IRL4YOU BOX

Mit dem Bereich **Chat** liest und schreibst du im Twitch-Chat deines Kanals, direkt in der Oberfläche der Box
(Port 8780). Du kannst moderieren, Follows und Kanalpunkte als Karten sehen und dich bei leerem Kamera-Akku
im Chat warnen lassen. Alles ist freiwillig; ohne Anmeldung kannst du den Chat nur lesen.

**Ehrlicher Stand (Beta):** Die Anmeldung und das Senden einer Testnachricht hat der Entwickler auf einer echten Box
bestätigt. **EventSub** (Follows, Kanalpunkte), das **Senden über die Twitch-Schnittstelle** und das **Bot-Konto**
sind bisher nur gegen nachgebaute Testserver geprüft, nicht gegen echtes Twitch. Wenn etwas nicht klappt,
sag es bitte.

## 1. Mit Twitch anmelden

Du gibst dabei **kein Passwort und keinen Token** in die Box ein. Die Anmeldung läuft wie am Fernseher mit einem Code.

1. Öffne in der Oberfläche den Bereich **Chat** und tippe auf **Mit Twitch anmelden**.
2. Die Box zeigt einen **Code**, einen **Link** (twitch.tv/activate) und einen **QR-Code**.
3. Öffne den Link (oder scanne den QR-Code mit dem Handy) und melde dich **auf twitch.tv** an.
4. Gib dort den Code ein, falls Twitch danach fragt, und bestätige die angezeigten Rechte.
5. In der Box steht kurz „Warte auf die Bestätigung bei Twitch …“, danach „Angemeldet als …“.
   Mit **Abbrechen** brichst du den Vorgang ab.

Die Anmeldung wird im Hintergrund erneuert. Wird sie **30 Tage lang nicht benutzt**, läuft sie ab
(„Anmeldung abgelaufen“); dann meldest du dich einfach neu an.

## 2. Chat lesen

- Der Chat zeigt den Kanal, mit dem du angemeldet bist. Lesen geht auch ohne Anmeldung.
- Du siehst Uhrzeit, farbige Namen und **Abzeichen** (Streamer, Mod, VIP, Abonnent).
- **Emotes** von Twitch, **7TV, BetterTTV und FrankerFaceZ** erscheinen als Bild. Ohne Verbindung zu diesen Diensten
  bleibt das Wort als Text stehen.
- **Ereigniskarten:** Sub, Resub, Geschenk-Abos (eine Sammelaktion als eine Karte), Raid, Cheer (Bits) und Ankündigungen
  erscheinen als farbige Karte; neue leuchten kurz auf. Dazu kommen Follow und Kanalpunkte, wenn du sie einschaltest
  (Abschnitt 5). Hast du hochgescrollt, erscheint „Neues Ereignis ↓“.
- Kopfzeile: links ein Verlauf der Senderate der letzten 30 Sekunden (grün gut, gelb mäßig, rot schlecht, grau keine
  Sendung), rechts bis zu vier Punkte, einer je Kamera.

**Ansicht anpassen:** Tippe oben rechts auf das **Zahnrad (⚙)**. Dort stellst du ein:
**Uhrzeit**, **Abzeichen**, **Emotes als Bild**, **Trennlinien**, **Signal bei Ereignissen** (zwei kurze Töne und
Vibration, standardmäßig aus) und die **Schrift** (klein, normal, groß). Die Auswahl gilt für dein Gerät.

## 3. Schreiben

Nach der Anmeldung erscheint unter dem Chat ein Eingabefeld.

1. Nachricht eintippen (bis 450 Zeichen), **Senden** tippen.
2. Hat Twitch die Nachricht verworfen (zum Beispiel wegen Spam- oder Tempofilter, nur Abonnenten, langsamer Modus),
   nennt die Box den Grund von Twitch in der Meldung.

Texte, die mit `/` oder `.` beginnen, werden nie als normale Nachricht gesendet. Erlaubt sind nur die
Moderationsbefehle aus Abschnitt 4.

## 4. Moderation

Die Moderation ist **aus**, bis du sie einschaltest. Dein Konto muss **Streamer oder Moderator** des Kanals sein.

1. Tippe auf das **Zahnrad (⚙)** und dann auf **Moderation einschalten**.
2. Twitch fragt einmal nach **neuen Rechten**. Bestätige sie wie bei der Anmeldung (Code und Link, Abschnitt 1).
   Danach steht hinter dem Zahnrad „Angemeldet als … · Moderation an“.
3. An den Nachrichten **anderer** Zuschauer erscheint ein **⋯**. Dahinter findest du: **Löschen**, **Alle löschen**
   (räumt alle Nachrichten der Person ab), **Timeout 10 Min**, **Timeout 1 Std** und **Bannen**.
4. Es gibt keine Rückfrage, dafür nach Timeout und Bann **Rückgängig** (kurz sichtbar). Bei „Alle löschen“ gibt es
   kein Rückgängig.
5. Im Eingabefeld gehen auch `/ban Name`, `/timeout Name [Sekunden]` und `/unban Name`.

**Ausschalten:** Zahnrad, **Moderation ausschalten**. Die ⋯ und die Befehle sind sofort weg; **Moderation einschalten**
geht danach ohne neue Bestätigung. Twitch erlaubt aber nicht, einem Zugang Rechte wieder wegzunehmen. Sie fallen
erst weg, wenn du dich **abmeldest und ohne Moderation neu anmeldest**.

## 5. Ereignisse einschalten (Follows und Kanalpunkte)

Follows und Kanalpunkte-Einlösungen kommen nicht aus dem Chat, sondern über EventSub von Twitch.

1. Zahnrad, **Ereignisse einschalten**.
2. Bestätige bei Twitch die **neuen Rechte** (Abschnitt 1). Danach steht dort „Ereignisse an“.
3. Es erscheinen Karten „Follow“ und „Kanalpunkte“ (mit Belohnung und Text des Zuschauers).

Das geht nur für deinen **eigenen** Kanal. Subs, Geschenk-Abos, Raids und Cheers brauchen diesen Schalter nicht.
Die Moderation bleibt, falls eingeschaltet, erhalten.

## 6. Akku-Warnung im Twitch-Chat (nur DJI)

Fällt der Akku einer per Bluetooth verbundenen **DJI-Kamera** unter die Schwelle, schreibt die Box eine Nachricht in
den Chat. Eine Kamera nur am HDMI-Kabel meldet keinen Akku; hier hilft der Schalter „Nur Akkustand lesen“ in der
Karte der DJI-Kamera.

**Einrichten:** Karte **Kameras**, Untermenü **Akku-Warnung im Twitch-Chat (nur bei DJI)**.

1. **Einschalten** ankreuzen.
2. **Warnen bei (%)**: die Schwelle (Standard 10 %).
3. **Nachricht**: dein Text. `{Kamera}` wird durch den Kameranamen, `{Prozent}` durch den Akkustand ersetzt.
   Standard: „Akkustand niedrig, bitte Akku wechseln: {Kamera} ({Prozent} %)“.
4. **Nur während der Sendung** angekreuzt: Gewarnt wird nur, solange gesendet wird.
5. **Speichern**, dann **Testnachricht senden**. Sie lautet „Test: IRL4YOU BOX“. Der Knopf geht erst nach dem
   Speichern. Unter dem Menü steht die letzte Meldung mit Uhrzeit oder der Fehler.

Pro Kamera kommt eine Warnung, bis sie lädt oder ihr Akku mindestens 10 Prozentpunkte über der Schwelle liegt
(zum Beispiel nach dem Akkuwechsel). Schlägt das Senden fehl, versucht es die Box ein paar Mal erneut.

**Wer schreibt?**

- **Bot-Konto:** Mit **Bot-Konto anmelden** meldest du ein zweites Twitch-Konto per Code an. Wähle bei Twitch das
  Bot-Konto, am besten in einem privaten Browserfenster. Der Bot bekommt nur die Rechte zum Lesen und Schreiben im
  Chat, keine Moderation und keine Ereignisse. **Bot abmelden** entfernt ihn wieder.
- **Ohne Bot** schreibt dein **angemeldetes Hauptkonto**.
- **Ohne beides** gilt ein von Hand eingetragener Token (Recht `chat:edit`, wie bei NOALBS). Er muss zum Bot-Konto
  gehören. Einfacher ist die Anmeldung per Code.
- Ist der Chat **nur für Follower oder Abonnenten**, muss der Bot **Moderator oder VIP** sein.
- Der Chat in der Oberfläche (Lesen, Schreiben, Moderation) bleibt immer beim Hauptkonto, nie beim Bot.
- Ist die Anmeldung des Bots abgelaufen, schweigt die Meldung, bis du ihn neu anmeldest oder abmeldest.
  Sie schreibt nie heimlich mit dem Hauptkonto.
- Ist nur der Bot angemeldet, trägst du den **Kanal** selbst ein.

## 7. Der Chat nutzt die Sendewege der Box

Die Verbindung des Chats zu Twitch läuft über die **Sendewege** der Box (zum Beispiel Mobilfunk): zuerst über den
besten Weg, bei Fehler oder Stille über den nächsten, zuletzt über die normale Verbindung. Ein toter Weg fällt nach
etwa 100 Sekunden auf. Eine einzelne Chat-Verbindung wird nicht auf mehrere Wege verteilt, sie weicht nur aus.

## 8. Sicherheit

- Deine Twitch-Zugangsdaten bleiben **auf der Box** (Dateien mit Zugriff nur für den Dienst). Sie erscheinen nie in der
  Oberfläche, in Meldungen oder Protokollen.
- Die Box kennt dein Twitch-Passwort nie; du bestätigst alles auf twitch.tv selbst.
- In die Einstellungssicherung kommt die Akku-Warnung **ohne Token** und ohne Anmeldung.
- **Abmelden:** Zahnrad, **Abmelden**. Der Bot hat sein eigenes **Bot abmelden**.

## 9. Wenn etwas nicht geht

| Meldung oder Problem | Ursache und Lösung |
|---|---|
| „Kein Kanal in der Twitch-Karte eingetragen.“ | Es ist kein Kanal bekannt. Mit dem Hauptkonto anmelden oder den Kanal in der Akku-Warnung eintragen. |
| „Keine Verbindung zu Twitch.“ | Die Box hat kein Internet oder Twitch ist nicht erreichbar. Sendewege und Netz prüfen. |
| „Noch keine Nachrichten.“ | Verbunden, aber im Chat wurde noch nichts geschrieben. |
| Kein Eingabefeld | Du bist nicht angemeldet. Zahnrad oder **Mit Twitch anmelden**. |
| „Zuerst mit Twitch anmelden“ | Siehe Abschnitt 1. |
| „Anmeldung abgelaufen, bitte neu anmelden“ | Neu anmelden (Abschnitt 1). Das kann nach 30 Tagen ohne Nutzung passieren. |
| „Die Anmeldung wurde abgelehnt oder ist abgelaufen“ | Der Code galt nicht mehr oder du hast auf twitch.tv abgelehnt. Neu anfangen. |
| „Twitch hat die Nachricht nicht gesendet: …“ | Twitch hat sie verworfen. Der Grund steht dahinter (Spamfilter, nur Abonnenten, langsamer Modus …). |
| „Verbindung gestört, unklar ob es angekommen ist“ | Schau im Chat nach, ob die Nachricht da ist, bevor du sie erneut sendest. |
| „Moderation ist noch nicht eingeschaltet“ | Zahnrad, **Moderation einschalten** (Abschnitt 4). |
| „Dazu fehlt die Berechtigung (Moderator im Kanal?)“ | Das Konto ist weder Streamer noch Moderator des Kanals, oder die Rechte fehlen. |
| Keine Follow- oder Kanalpunkte-Karten | Zahnrad, **Ereignisse einschalten** und bestätigen. Gilt nur für den eigenen Kanal. |
| Akku-Warnung kommt nicht | Eingeschaltet und gespeichert? Kamera per Bluetooth verbunden? Bei „Nur während der Sendung“ muss gesendet werden. Mit **Testnachricht senden** prüfen. |
| Testnachricht-Knopf grau | Erst **Speichern**; außerdem muss ein Kanal und ein Konto (oder Token) bekannt sein. |
| Bot schreibt nicht | Bot neu anmelden („Anmeldung des Bots abgelaufen“). Bei Chat nur für Follower oder Abonnenten: Bot zum Moderator oder VIP machen. |
| Chat zeigt Emotes als Text | 7TV, BTTV oder FFZ nicht erreichbar, oder **Emotes als Bild** ist im Zahnrad aus. |
| Bildschirm geht am Handy aus | Der Schalter „Bildschirm anlassen“ (Glühbirne in der Kopfzeile) braucht https, zum Beispiel über den Fernzugriff mit Tailscale. |
