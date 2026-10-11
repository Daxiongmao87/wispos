#!/bin/sh
# Dev: launch BIT.TRIP RUNNER the way the tests do, with NO auto-stop: tracing off, pad shared, Gallium HUD (frame times dumped), persistent Mesa cache.
W=/storage/wispos/dev; H=/storage/wispos/runtime/steam/steamapps/compatdata/63710/hud
sh $W/kill-game.sh; rm -rf $H; mkdir -p $H
DB=$(tr '\0' '\n' < /proc/$(pidof steam | cut -d' ' -f1)/environ | grep '^DBUS_SESSION_BUS_ADDRESS=' | cut -d= -f2-)
cd $W; REPLAY_ENV="DBUS_SESSION_BUS_ADDRESS=$DB WINEDEBUG=-all PROTON_LOG=0 PRESSURE_VESSEL_FILESYSTEMS_RO=/run/udev MESA_SHADER_CACHE_DIR=/storage/wispos/runtime/steam/steamapps/compatdata/63710/mesa_cache PROTON_USE_WINED3D=1 WINEDLLOVERRIDES=d3dx9_43,d3dcompiler_43=n,b GALLIUM_HUD=fps,frametime GALLIUM_HUD_PERIOD=500000 GALLIUM_HUD_DUMP_DIR=$H" REPLAY_TAG=-play REPLAY_TIMEOUT=7200 setsid nohup python3 replay.py game >/dev/null 2>&1 < /dev/null &
echo "$(date +%T) game launched (no auto-stop); stop it with: sh $W/kill-game.sh"
