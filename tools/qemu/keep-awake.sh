#!/bin/bash
# Persistent idle policy for the explicitly configured development VM user.
set -euo pipefail
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -u) != 0 ]]
export DISPLAY=${DISPLAY:-:0}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}
gsettings set org.ukui.screensaver idle-activation-enabled false
gsettings set org.ukui.screensaver idle-lock-enabled false
gsettings set org.ukui.screensaver sleep-activation-enabled false
gsettings set org.ukui.screensaver idle-delay 0
gsettings set org.ukui.session idle-delay 0
for key in sleep-computer-ac sleep-computer-battery sleep-computer-ups \
 sleep-display-ac sleep-display-battery sleep-display-ups \
 hibernate-computer-ac hibernate-computer-battery; do
 gsettings set org.ukui.power-manager "$key" 0
done
gsettings set org.ukui.power-manager idle-dim-ac false
gsettings set org.ukui.power-manager idle-dim-battery false
xset s off
xset s noblank
xset -dpms 2>/dev/null || true # KasmVNC has no DPMS extension.
[[ ${1:-} == --hold ]] || exit 0
# UKUI's current session can retain an already registered idle timer. Keep a
# standard session inhibitor alive as well as persisting the settings above.
exec python3 - <<'PY'
import signal
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
session = dbus.Interface(bus.get_object('org.gnome.SessionManager',
                         '/org/gnome/SessionManager'), 'org.gnome.SessionManager')
cookie = session.Inhibit('xr-development', dbus.UInt32(0),
                         'XR development desktop idle policy', dbus.UInt32(12))
loop = GLib.MainLoop()
for signum in (signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, lambda *_: loop.quit())
try:
    loop.run()
finally:
    session.Uninhibit(cookie)
PY
