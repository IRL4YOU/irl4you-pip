# Lizenz für das Erkennungsmodul / Licence for the detection module

*Fassung 1 vom 21. Oktober 2026. Diese Lizenz gilt nur für die unten genannten Teile (Karte „Datenschutz“); alles andere in IRL4YOU BOX
bleibt unter seiner bisherigen Lizenz (`LICENSE`, `NOTICE.md`). Bei Abweichungen zwischen dem deutschen und dem englischen Text gilt der deutsche.*

Copyright (c) 2026 IRL4YOU. Alle Rechte vorbehalten, soweit sie unten nicht ausdrücklich eingeräumt werden.

**Hinweis zur Art der Lizenz:** Diese Lizenz ist **keine Open-Source-Lizenz** im Sinne der Open Source Initiative, weil sie die Nutzung
für bestimmte Personengruppen (Unternehmen) und Zwecke (Verkauf) beschränkt. Der Quelltext ist einsehbar, die Nutzung ist beschränkt
(„source available“).

## Deutsch

### 1. Gegenstand

Diese Lizenz gilt für das **Erkennungsmodul** von IRL4YOU BOX (Karte „Datenschutz“), das Köpfe und Kennzeichen im Bild der Hauptkamera findet und
verpixeln lässt:

- die Programme des Erkennungsdienstes (`pipbox_privacy.py`, der Ordner `privacy/`, `install/pipbox-privacy.py`), in jeder Form, auch übersetzt oder als Bytecode,
- die Modelldateien im Ordner `privacy-modelle/` (`kopf_fp16.rknn`, `kennz_fp16.rknn`, `zweit_fp16.rknn`), also die trainierten Netze mit ihren
  Gewichten, in allen Fassungen, auch abgeleitet oder übersetzt in andere Formate (zum Beispiel ONNX, RKNN, TensorRT),
- die Trainings- und Prüfprogramme und die Trainingsdaten des Rechteinhabers (nicht Teil dieses Repositorys).

Für alle übrigen Teile von IRL4YOU BOX, auch für den Bildbaustein `gst/gstpbpixel.c` und die Karte in der Oberfläche, gilt weiter die
**MIT-Lizenz** (siehe `LICENSE`) beziehungsweise die in `NOTICE.md` genannte Lizenz des jeweiligen Urhebers.

### 2. Was erlaubt ist (kostenlos)

**Privatpersonen** und **Streamer** dürfen das Erkennungsmodul kostenlos

- auf **ihrer eigenen** Hardware für **ihre eigenen** Livestreams und Videos benutzen, und zwar **nur zum Eigengebrauch**,
- dafür auf ihren eigenen Geräten installieren (vervielfältigen) und für den eigenen Gebrauch ändern.

Jeder Nutzer bezieht das Erkennungsmodul **selbst** über die Projektseite oder das Update von IRL4YOU BOX. **Weitergabe und Verbreitung** an andere,
auch kostenlos, auch an Privatpersonen und Streamer, und das Einrichten auf Geräten anderer sind **nicht erlaubt** (Abschnitt 3 Nummer 4).

**Streamer** ist eine **natürliche Person** (auch als Einzelunternehmer oder Freiberufler), die **ihre eigenen** Streams oder Videos
veröffentlicht. Einnahmen aus diesen Streams (Spenden, Abonnements, Werbung, Sponsoring, Plattformvergütungen) sind **ausdrücklich
erlaubt** und machen die Nutzung nicht gewerblich im Sinne von Abschnitt 3.

**Ausnahme Bittersweet1987:** Der GitHub-Nutzer Bittersweet1987, Mitwirkender an IRL4YOU BOX, darf das Erkennungsmodul wie eine
Privatperson oder ein Streamer nach Abschnitt 2 benutzen, auch in seinen eigenen Projekten, und **braucht dafür nicht zu fragen**. Er
darf diese Lizenz **nicht ändern**: keine Neulizenzierung, keine Unterlizenzen mit anderen Bedingungen, keine Entfernung der Hinweise
(Abschnitt 3 Nummer 5 gilt auch für ihn). Für ihn gilt Abschnitt 3 Nummer 4 nicht (Ausnahme). Liefert er das Erkennungsmodul zusammen mit seinen Projekten aus, bleibt es auch dann unter
**dieser** Lizenz (Lizenzdatei beilegen); seine übrigen Teile behalten ihre eigene Lizenz.

### 3. Was nicht erlaubt ist

1. **Unternehmen und Organisationen** (zum Beispiel GmbH, UG, AG, Verein, Sender, Agentur, Produktionsfirma, Veranstalter) dürfen das
   Erkennungsmodul in **keiner Form** benutzen, insbesondere nicht **installieren, betreiben, testen, weitergeben** und nicht **bewerben oder
   vermarkten**, auch nicht im Auftrag Dritter oder für Kunden. Das gilt auch, wenn ein Mitarbeiter es im Auftrag eines Unternehmens benutzt.
2. **Verkauf, Vermietung, Lizenzierung** des Erkennungsmoduls, auch als Teil eines Produkts: Geräte (auch vorinstalliert), Software, Apps,
   Abonnements, Online-Dienste, Dienstleistungen für Kunden, Datenträger oder Downloads gegen Entgelt. **Das Erkennungsmodul darf nicht
   verkauft werden.**
3. Das Erkennungsmodul oder seine Gewichte zu verwenden, um **andere Erkennungsmodelle** zu trainieren, abzuleiten, zu destillieren oder zu
   verbessern, die für Zwecke nach Nummer 1 oder 2 eingesetzt werden, und die Gewichte aus der Box zu extrahieren, um sie in anderen
   Produkten einzusetzen.
4. Das Erkennungsmodul an Dritte **weiterzugeben, zu verbreiten oder zugänglich zu machen** (auch kostenlos, auch an Privatpersonen oder
   Streamer), auf Geräten anderer zu installieren oder in **Betriebssystem-Abbilder, Geräte oder Pakete** einzubauen, die an Dritte abgegeben
   werden. Es dient nur dem Eigengebrauch auf eigenen Geräten.
5. Urheber-, Lizenz- und Herkunftshinweise zu **entfernen** oder zu verändern.

### 4. Kontakt, keine Sondervereinbarungen

Unternehmen und Organisationen haben **vor jeder Nutzung beim Rechteinhaber oder bei Bittersweet1987 nachzufragen** (über die Projektseite auf GitHub,
https://github.com/IRL4YOU/irl4you-pip, Issue oder Diskussion). Ohne ausdrückliche Zustimmung dürfen sie das Erkennungsmodul nicht nutzen.
**Ein Anspruch auf Zustimmung besteht nicht.**

Es gibt **keine Sondervereinbarungen**: Weder mündliche noch stillschweigende Abreden, noch Zusagen Dritter ändern diese Lizenz. Abweichungen
gelten nur, wenn der Rechteinhaber sie **schriftlich** (Textform genügt, ausdrücklich für den Einzelfall und für diesen Text) bestätigt hat.
Duldung eines Verstoßes ist keine Genehmigung. Die Ausnahme in Abschnitt 2 (Bittersweet1987) ist Teil dieser Lizenz und keine Sondervereinbarung.

### 5. Beiträge

Wer Änderungen oder Verbesserungen am Erkennungsmodul an den Rechteinhaber einreicht (zum Beispiel als Pull Request), räumt dem
Rechteinhaber daran ein einfaches, unbefristetes, weltweites Nutzungsrecht ein, sie unter dieser Lizenz (auch in geänderter Fassung)
weiterzugeben. Das Urheberrecht des Beitragenden bleibt unberührt.

### 6. Keine Datenübertragung

Das Erkennungsmodul arbeitet lokal auf dem Gerät und **sendet keine Bilder oder Daten** an den Rechteinhaber oder Dritte.

### 7. Keine Garantie, Verantwortung der Nutzer, Haftung

Das Erkennungsmodul ist ein **Hilfsmittel**. Es findet nicht jeden Kopf und nicht jedes Kennzeichen, und es kann fälschlich etwas als Kopf
oder Kennzeichen melden. **Die Erkennung ist nicht zu 100 % zuverlässig.** Der Einsatz erfolgt **ausschließlich auf eigene Verantwortung und eigenes Risiko**; für nicht oder falsch verpixelte Köpfe und Kennzeichen trägt allein der Nutzer die Verantwortung. Wer streamt, bleibt **selbst verantwortlich** dafür, dass Personen und Kennzeichen im Bild rechtmäßig behandelt
werden (zum Beispiel nach Datenschutz- und Persönlichkeitsrecht). Das Erkennungsmodul wird **ohne jede Gewährleistung** bereitgestellt, soweit
das Gesetz dies zulässt. Der Rechteinhaber haftet nicht für Schäden aus der Nutzung, außer bei Vorsatz oder grober Fahrlässigkeit und in den
Fällen, in denen das Gesetz eine Haftung zwingend vorschreibt (zum Beispiel für Schäden an Leben, Körper und Gesundheit).

### 8. Beendigung

Bei einem Verstoß gegen diese Lizenz enden die Rechte nach Abschnitt 2 für den Verletzer **automatisch**, bis der Rechteinhaber sie
ausdrücklich wieder einräumt. Rechte anderer Nutzer, die ihre Kopie vom Rechteinhaber bezogen haben, bleiben unberührt, solange sie selbst
diese Lizenz einhalten. Weitergehende Ansprüche des Rechteinhabers (Unterlassung, Schadensersatz) bleiben unberührt.

### 9. Fremde Bausteine, Recht, Schlussbestimmungen

Fremde Bausteine behalten ihre Lizenzen (siehe `NOTICE.md`). Es gilt **deutsches Recht** unter Ausschluss des UN-Kaufrechts; zwingende
Verbraucherschutzvorschriften des Landes, in dem ein Verbraucher seinen gewöhnlichen Aufenthalt hat, bleiben unberührt. Sollte eine
Bestimmung dieser Lizenz unwirksam sein, bleibt der Rest wirksam; an die Stelle der unwirksamen Bestimmung tritt die gesetzlich zulässige
Regelung, die dem wirtschaftlichen Zweck am nächsten kommt. Bei Abweichungen zwischen dem deutschen und dem englischen Text gilt der deutsche.

## English

*Convenience translation; the German text prevails. This licence is not an open-source licence (it restricts use by companies and sale): the
source is available, use is restricted.*

### 1. Subject

This licence covers the **detection module** of IRL4YOU BOX ("Privacy" card) that finds heads and licence plates in the main camera's picture
and has them pixelated: the programs of the detection service (`pipbox_privacy.py`, the folder `privacy/`, `install/pipbox-privacy.py`) in any form, including compiled or translated, the model files in `privacy-modelle/` (`kopf_fp16.rknn`, `kennz_fp16.rknn`,
`zweit_fp16.rknn`, i.e. the trained networks and their weights) in every version including derived or converted formats (e.g. ONNX, RKNN,
TensorRT), and the licensor's training and evaluation programs and training data (not part of this repository). All other parts of IRL4YOU BOX,
including the picture element `gst/gstpbpixel.c` and the card in the user interface, remain under the MIT licence (`LICENSE`) or the licence
named in `NOTICE.md`.

### 2. Permitted use (free of charge)

**Private persons** and **streamers** may, free of charge, use the module on their own hardware for **their own** live streams and videos,
install it on their own devices and modify it for their own use, **for personal use only**. Everyone obtains the module **themselves**
through the project page or the IRL4YOU BOX update. **Passing it on or distributing it** to others, even free of charge and even to private
persons and streamers, and setting it up on other people's devices are **not permitted** (section 3 number 4). A **streamer** is a **natural person** (including a sole proprietor or freelancer) who publishes **their own** streams
or videos. Income from these streams (donations, subscriptions, advertising, sponsorship, platform payouts) is **expressly permitted** and
does not make the use commercial under section 3.

**Exception Bittersweet1987:** The GitHub user Bittersweet1987, a contributor to IRL4YOU BOX, may use the module like a private person or
streamer under section 2, also in his own projects, **without asking**. He may **not change this licence**: no relicensing, no sublicences on
other terms, no removal of notices. Section 3 number 4 does not apply to him (exception). If he ships the module together with his projects, it stays under **this** licence (include the licence
file); his other parts keep their own licence.

### 3. Not permitted

1. **Companies and organisations** (e.g. corporations, associations, broadcasters, agencies, production companies, event organisers) may
   not use the module **in any form**, in particular not **install, operate, test or pass it on**, nor **advertise or market** it, also not on behalf of third parties or for
   customers, including use by an employee on behalf of a company.
2. **Selling, renting or licensing** the module, also as part of a product: devices (including pre-installed), software, apps, subscriptions,
   online services, services for customers, paid media or downloads. **The module must not be sold.**
3. Using the module or its weights to **train, derive, distil or improve other detection models** that are used for purposes under 1 or 2,
   and extracting the weights from the device for use in other products.
4. **Passing on, distributing or making available** the module to others (even free of charge, even to private persons or streamers),
   installing it on other people's devices, or building it into **operating-system images, devices or packages** handed to third parties.
   The module is for personal use on one's own devices only.
5. **Removing or altering** copyright, licence and origin notices.

### 4. Contact, no special agreements

Companies and organisations must **ask the licensor or Bittersweet1987 before any use** (through the project page on GitHub,
https://github.com/IRL4YOU/irl4you-pip, issue or discussion). Without express consent they may not use the module. **There is no right to consent.** There are **no special agreements**: no
oral or implied arrangements and no statements by third parties change this licence. Deviations apply only if the licensor has confirmed them
**in writing** (text form suffices) expressly for the individual case and for this text. Tolerating a breach is not permission. The exception in
section 2 (Bittersweet1987) is part of this licence and not a special agreement.

### 5. Contributions

Anyone who submits changes or improvements to the module to the licensor (e.g. a pull request) grants the licensor a non-exclusive,
perpetual, worldwide right to pass them on under this licence (also in modified form). The contributor's copyright remains unaffected.

### 6. No data transmission

The module works locally on the device and **does not send images or data** to the licensor or third parties.

### 7. No warranty, users remain responsible, liability

The module is an **aid**. It does not find every head or licence plate and may report false detections. **The detection is not 100 % reliable.** It is used **solely at your own responsibility and risk**; the user alone is responsible for heads and licence plates that are not or wrongly pixelated. Streamers remain **solely
responsible** for handling persons and licence plates in their video lawfully (e.g. under data protection and personality rights). The module is
provided **without warranty of any kind** to the extent permitted by law. The licensor is not liable for damages arising from its use, except in
cases of intent or gross negligence and where liability is mandatory by law (e.g. injury to life, body or health).

### 8. Termination

In case of breach, the rights under section 2 end **automatically** for the infringer until the licensor expressly grants them again. Rights of
other users who obtained their copy from the licensor remain unaffected as long as they themselves comply with this licence. Further claims
of the licensor (injunction, damages) remain unaffected.

### 9. Third-party components, law, final provisions

Third-party components keep their own licences (see `NOTICE.md`). **German law** applies, excluding the UN Convention on Contracts for the
International Sale of Goods; mandatory consumer protection rules of the consumer's country of habitual residence remain unaffected. If a
provision is invalid, the remainder stays effective and the invalid provision is replaced by the permitted provision closest to its economic
purpose. In case of discrepancies between the German and the English text, the German prevails.
