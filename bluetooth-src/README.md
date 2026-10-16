# Quellen des Bluetooth-Treibers (btusb)

Diese Dateien sind **unverändert** aus dem Linux-Kernel **v5.10.160** (`drivers/bluetooth/btusb.c`, `btintel.h`, `btbcm.h`, `btrtl.h`) und stehen
unter der **GPL-2.0** (siehe `COPYING` und die Kopfzeilen der Dateien). Sie gehören nicht zur MIT-Lizenz dieses Projekts.

Der Helfer `install/pipbox-btdriver.py` baut daraus auf der Box das Modul `btusb` neu, mit einigen zusätzlichen USB-Kennungen für Realtek-Sticks
(zum Beispiel TP-Link UB500), die der Kernel 5.10 nicht kennt. Die einzige Änderung am Quelltext sind zusätzliche Tabellenzeilen
`{ USB_DEVICE(0x…, 0x…), .driver_info = BTUSB_REALTEK },` hinter dem Eintrag für `0x0bda:0xb009`; der Helfer fügt sie beim Bau ein
(Funktion `patch_source`) und prüft vorher die SHA-256-Summen dieser Dateien. Das Ergebnis ist ein Modul `btusb.ko`, das nach
`/lib/modules/<Kernel>/updates/` kommt; das Standardmodul des Kernels bleibt unangetastet.

Herkunft: <https://github.com/gregkh/linux/tree/v5.10.160/drivers/bluetooth> (gleiche Dateien von `git.kernel.org`, stable-Zweig, Tag v5.10.160, geprüft).

| Datei | SHA-256 |
|---|---|
| btusb.c | `5dbce6032028414fe89067e13ba3e3d4bf9dfb5a0e592771d8c35f68dfd50626` |
| btintel.h | `fa261ff6177abfe2d29fed0af0aa93344d0f11c51cb9ea7a9804563dc11ae5db` |
| btbcm.h | `5006497bf37ef95acc186f7c62b51ddbaaecdfcf09bb46f2d250dbc39c93f0b4` |
| btrtl.h | `759be416a2bbe7d20e98b06f1273b8a7e654770251865e7a049fe6a9c5700298` |
