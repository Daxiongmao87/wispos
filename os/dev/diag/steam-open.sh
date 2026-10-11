#!/bin/bash
# Dev: open a steam:// URL in the RUNNING Steam client, ON the device (copy to /storage/wispos/dev/). usage: steam-open.sh steam://nav/games/details/2200
# Why: opens an owned game's library page (Install/Play) without driving the UI to it; steam://install/<appid> opens the install dialog. The client forwards the URL to the running
# instance and exits 0 (it starts nothing when Steam is not running). Steam must be running; the screen must be awake and not on the screensaver (docs/WISPOS-NOTES.md playbook item 6:
# one pad press first). It needs Steam's own environment, so it is copied from the running client's /proc/<pid>/environ (how ownership of Carmageddon 2 was checked).
URL=${1:?usage: steam-open.sh steam://...}
PID=$(pidof steam | cut -d' ' -f1); [ -n "$PID" ] || { echo "steam is not running"; exit 1; }
python3 - "$PID" "$URL" <<'PY'
import os, sys
pid, url = sys.argv[1:3]
env = dict(kv.split("=", 1) for kv in open("/proc/%s/environ" % pid, "rb").read().decode(errors="replace").split("\0") if "=" in kv)
os.chdir("/storage/wispos/runtime/steam")
os.execve("./steamrtarm64/steam", ["./steamrtarm64/steam", "-ifrunning", url], env)
PY
