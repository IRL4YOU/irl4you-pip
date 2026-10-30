#!/usr/bin/env python3
import argparse
import base64
import binascii
import fcntl
import glob
import hashlib
import ipaddress
import hmac

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import controller_keys
import hdmi_daemon
import pipbox_live
from tw.emotes import (ThirdPartyEmotes)
from tw.events import (TwitchEvents, WebSocketLite)
from tw.irc import (ChatPaths, TwitchChat, _IrcLines, _ascii_digits)
from tw.login import (ModSessions, TwitchAccounts, TwitchBotLogin, TwitchLogin, TwitchModLogin)
from tw.mod import (HelixChat, TwitchMod, TwitchSender)
from tw.notifier import (TwitchNotifier)
from tw.reader import (TwitchReader)
from tw.store import (TwitchStore)
from tw.sysalerts import (SysAlerts)
try:
    import pipbox_preview
except ImportError:
    pipbox_preview = None

from srv.access import (ExtraAddress, PreviewAccess, SupportCases, TAILNET, UiAccess, client_origin, source_allowed)
from srv.auth import (Auth, FAIL_WINDOW, MAX_CHECKS, MAX_FAILS, REMEMBER_SECONDS, SESSION_SECONDS)
from srv.cameras import (CameraStore, HDMI_KEY, KEY_RE, ROLES)
from srv.datenschutz import (DATENSCHUTZ_BAUSTEIN, DATENSCHUTZ_DATEI, DATENSCHUTZ_MODELL, DATENSCHUTZ_PY, DatenschutzStore, PBPIXEL_BOXES, PBPIXEL_TAP, PRIVACY_URL, SENDE_PIPELINE, datenschutz_aktiv, kette_signatur, neustart_noetig, pixel_stand, pixel_umschalten, privacy_status)
from srv.dji_service import (DJI_COMMANDS, DJI_FIELDS, DjiService)
from srv.handler import (Handler)
from srv.hdmi_service import (HdmiService)
from srv.hostctl import (AutoStart, BtDriver, Developer, LogBundle, LogMode, Power)
from srv.httpd import (LimitedHTTPServer, PHONE_FRAME, controller_action, scene_swap)
from srv.layout import (DeckStore, LayoutStore)
from srv.netinfo import (ARP_STATUS, ARP_STATUS_MAX_AGE, FIXED_ALIAS, IFACE_NAME_RE, LINKS_FILE, NET_LABELS, NET_PREFIXES, NET_RE, NET_SKIP, _alert_text, _iface_ips_raw, fixed_ip, iface_ips, lan_ip, mac_suffix, net_conflict, net_label, uplink_states)
from srv.pipeline import (DEFAULT_MAIN_DELAY_MS, DEFAULT_PIP_DELAY_MS, DELAY_KEYS, FRAME_MS, PipelineStore, always_compatible, view_only_change)
from srv.plugin import (CAM_LIVE, FEED_AUDIO_CAPS, FEED_FILL_VIDEO, FEED_PORT, FEED_VIDEO_CAPS, FREE, PIP_CORNERS, PLUGIN_SO, SEND_STATUS, SWAP_SELECT, SWAP_STATE, VIEW_ENABLED, VIEW_FILE, VIEW_STATE, _CENTER, _FILL, _MULTI, _STYLE, _SWAP, _VIEW, feeder_tool, pip_corners, plugin_fill, plugin_multi, plugin_style, plugin_swap, plugin_view)
from srv.remote import (Remote)
from srv.sampler import (BC_STATS_FILE, LIMITS, NPU_FILE, OutageWatch, Sampler, THERMAL_NAMES, UsbWatch, devfreq_loads, disk_usage, npu_load, send_stats)
from srv.sendctl import (SendControl)
from srv.settings import (SETTINGS_FORMAT, SETTINGS_SECTIONS, SETTINGS_VERSION, SettingsTransfer, client_wifi_list)
from srv.srtla import (HOST_RE, PENDING_LABELS, SrtlaStore, srtla_signature)
from srv.stats import (FpsProbe, StatsServer, StatsShare, build_stats_snapshot)
from srv.status import (FpsMemory, IfaceMemory, NetChoice, UplinkMemory, status_light)
from srv.style import (CROP_KEEP, CROP_REF_H, CROP_REF_W, HW_MIN_H, HW_MIN_W, SIZE_MAX, SIZE_MIN, STYLE_COLOR_RE, STYLE_DEFAULT, STYLE_SLOTS, _style_copy, clean_style, clean_styles, pip_size, small_decode, style_text)
from srv.swupdate import (SwUpdate, Updates)
from srv.util import (WEB_DIR, _TTL, read, ttl_cached, ttl_cached_drop)
from srv.vault import (Vault)
from srv.versions import (KEY_PACKAGES, VERSION_RE, belacoder_running, installed_versions, vkey)
from srv.wifi import (DeviceNames, Wifi, label_bluetooth)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--demo", action="store_true", help="Beispielwerte statt /proc")
    ap.add_argument("--allow-public", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--state", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "state"),
                    help="Ordner für Passwort-Hash und Kameraliste")
    ap.add_argument("--bela-config", default="", help="belaUI config.json: BELABOX-Passwort mitbenutzen")
    ap.add_argument("--rtmp-port", type=int, default=1935, help="RTMP-Port, den Kameras nutzen")
    ap.add_argument("--rtmp-app", default="publish", help="nginx-rtmp-Applikation")
    ap.add_argument("--rtmp-stat-url", default="", help="nginx-rtmp-Statistik (XML), z. B. http://127.0.0.1/stat")
    args = ap.parse_args()
    Handler.updates = Updates(args.state, args.demo)
    if not args.demo:
        threading.Thread(target=Handler.updates.auto_loop, daemon=True).start()
    Handler.djisvc = None
    Handler.auth = Auth(args.state, args.bela_config or None,
                        demo=args.demo and args.host in ("127.0.0.1", "::1", "localhost"))
    Handler.cams = CameraStore(os.path.join(args.state, "cameras.json"), args.rtmp_app, args.rtmp_stat_url, args.demo)
    Handler.outages = OutageWatch()
    Handler.usbwatch = UsbWatch(os.path.join(args.state, "usb-events.json"), demo=args.demo)
    Handler.support = SupportCases(os.path.join(args.state, "support-cases.json"))
    Handler.sampler = Sampler(args.demo)
    Handler.sampler.sample()
    Handler.srtla = SrtlaStore(os.path.join(args.state, "srtla.json"))
    Handler.pipeline = PipelineStore(os.path.join(args.state, "pipeline.json"))
    Handler.send = SendControl(args.state, Handler.srtla, Handler.pipeline, Handler.cams, args.demo)
    Handler.send.block_fn = Handler.updates.live_block
    Handler.preview = pipbox_preview.Client() if pipbox_preview and not args.demo else None
    Handler.swupdate = SwUpdate(args.state, args.demo, Handler.send)
    if not args.demo:
        threading.Thread(target=Handler.swupdate.auto_loop, daemon=True).start()
    Handler.remote = Remote(args.state, args.demo)
    Handler.netchoice = NetChoice(os.path.join(args.state, "camera-net.json"))
    Handler.names = DeviceNames(args.state)
    Handler.layout = LayoutStore(args.state)
    Handler.datenschutz = DatenschutzStore(args.state)
    Handler.deck = DeckStore(args.state)
    Handler.statsshare = StatsShare(args.state, demo=args.demo)
    Handler.fpsprobe = FpsProbe("rtmp://127.0.0.1:%d/%s" % (args.rtmp_port, args.rtmp_app), os.path.join(args.state, "gst-registry.bin"), demo=args.demo)
    Handler.statsserver = StatsServer(Handler.statsshare, Handler.stats_snapshot_now)
    if not args.demo:
        Handler.statsserver.start()
    Handler.wifi = Wifi(args.state, args.demo, Handler.netchoice, Handler.names, Handler.srtla)
    Handler.power = Power(args.state, args.demo, Handler.send)
    Handler.logmode = LogMode(args.state, args.demo)
    Handler.btdriver = BtDriver(args.state, args.demo)
    Handler.logbundle = LogBundle(args.state, args.demo)
    Handler.developer = Developer(args.state, args.demo, args.bela_config or None)
    Handler.autostart = AutoStart(args.state, Handler.send, args.demo)
    if not args.demo:
        threading.Thread(target=Handler.autostart.run, daemon=True).start()
    Handler.cams.ipfn = Handler.netchoice.ip
    Handler.djisvc = DjiService(args.state, Handler.cams, args.rtmp_app, args.rtmp_port, args.demo)
    Handler.djisvc.pipeline = Handler.pipeline
    Handler.ckeys = controller_keys.ControllerKeys(args.state, lambda fn: controller_action(fn, Handler.pipeline, Handler.cams, Handler.send), demo=args.demo)
    Handler.ckeys.start()
    Handler.transfer = SettingsTransfer(args.state, Handler.cams, Handler.pipeline, Handler.srtla, Handler.autostart, Handler.names, Handler.djisvc,
                                        Handler.wifi, Handler.send, args.demo)
    def chat_sources():
        ups = list(((Handler.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
        ips = {x["iface"]: x["ip"] for x in iface_ips() if x.get("ip")}
        return [ips[u] for u in ups if u in ips]
    Handler.chatpaths = ChatPaths(chat_sources)
    Handler.transfer.netchoice = Handler.netchoice
    if not args.demo:
        mem = IfaceMemory(os.path.join(args.state, "iface-macs.json"))
        try:
            mem.reconcile(Handler.srtla, Handler.netchoice, Handler.cams, Handler.wifi, Handler.names)
        except Exception as e:
            print("iface_macs:", type(e).__name__, flush=True)
        threading.Thread(target=mem.run, args=(Handler.srtla, Handler.netchoice, Handler.cams, Handler.wifi, Handler.names), daemon=True).start()
    Handler.twitch = TwitchNotifier(TwitchStore(os.path.join(args.state, "twitch.json")), Handler.djisvc, Handler.cams, Handler.send, chat=TwitchChat(paths=Handler.chatpaths), demo=args.demo)
    Handler.sysalerts = SysAlerts(os.path.join(args.state, "sysalerts.json"), Handler.twitch, Handler.sampler, demo=args.demo)
    Handler.twitchlogin = TwitchLogin(os.path.join(args.state, "twitch-login.json"), demo=args.demo, paths=Handler.chatpaths)
    Handler.twitch.store.account = Handler.twitchlogin
    Handler.twitchbot = TwitchBotLogin(os.path.join(args.state, "twitch-bot-login.json"), demo=args.demo, paths=Handler.chatpaths)
    Handler.twitchsessions = ModSessions(args.state, demo=args.demo, paths=Handler.chatpaths)
    Handler.twitchaccounts = TwitchAccounts(Handler.twitchlogin, Handler.twitchsessions, store=Handler.twitch.store)
    Handler.twitch.store.bot = Handler.twitchbot
    if not args.demo:
        threading.Thread(target=Handler.twitchlogin.keep, daemon=True).start()
        threading.Thread(target=Handler.twitchbot.keep, daemon=True).start()
    Handler.chatreader = TwitchReader(Handler.twitch.store, demo=args.demo, paths=Handler.chatpaths)
    Handler.uplinkmem = UplinkMemory()
    Handler.fpsmem = FpsMemory()
    Handler.sysalerts.light_fn = Handler.light_now

    def fps_status():
        rates = Handler.send.measured_rates()
        if not rates:
            return {}
        names = {c.get("key"): c.get("name") for c in list(Handler.cams.cams) if isinstance(c, dict)}
        return {k: {"name": names.get(k) or k, "rates": list(v)} for k, v in rates.items()}
    def pixel_get():
        return pixel_stand(Handler.datenschutz)
    def pixel_set(an, kopf, kennz):
        return pixel_umschalten(Handler.datenschutz, Handler.send, Handler.pipeline, an, kopf, kennz)
    Handler.sysalerts.pixel_get, Handler.sysalerts.pixel_set = pixel_get, pixel_set
    Handler.sysalerts.fps_fn = fps_status
    Handler.sysalerts.reader = Handler.chatreader
    Handler.chatmod = TwitchMod(Handler.twitch.store, Handler.twitchlogin, paths=Handler.chatpaths, demo=args.demo, demo_reader=Handler.chatreader if args.demo else None)
    Handler.chathelix = HelixChat(Handler.twitchlogin, Handler.chatmod)
    Handler.chatsender = TwitchSender(Handler.twitch.store, chat=TwitchChat(paths=Handler.chatpaths), demo_reader=Handler.chatreader if args.demo else None, mod=Handler.chatmod, helix=Handler.chathelix)
    Handler.twitchaccounts.check = Handler.chatmod.is_moderator
    Handler.chatevents = TwitchEvents(Handler.twitch.store, Handler.twitchlogin, Handler.chatmod, Handler.chatreader, paths=Handler.chatpaths)
    if not args.demo:
        Handler.chatreader.events = Handler.chatevents
    Handler.hdmi = HdmiService(args.state, Handler.cams, args.demo)
    Handler.transfer.hdmi, Handler.transfer.twitch = Handler.hdmi, Handler.twitch.store
    if not args.demo:
        threading.Thread(target=Handler.twitch.run, daemon=True).start()
        threading.Thread(target=Handler.sysalerts.run, daemon=True).start()
        threading.Thread(target=Handler.sysalerts.run_cmd, daemon=True).start()

    def watcher():
        while True:
            try:
                Handler.cams.auto_add()
            except Exception as e:
                print("auto_add:", e)
            time.sleep(3)
    threading.Thread(target=watcher, daemon=True).start()
    Handler.uiaccess = UiAccess(os.path.join(args.state, "ui-access.json"), demo=args.demo)
    Handler.previewaccess = PreviewAccess(os.path.join(args.state, "preview-access.json"), demo=args.demo)
    Handler.netaddr = ExtraAddress(args.state, demo=args.demo)
    LimitedHTTPServer.ui_access = Handler.uiaccess
    LimitedHTTPServer.allow_public = args.allow_public
    srv = LimitedHTTPServer((args.host, args.port), Handler)
    print(f"PIPBOX auf http://{args.host}:{args.port} (demo={args.demo})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
