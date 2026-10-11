#!/bin/sh
# Dev: kill the leftover Wine/game/launch processes of a manual test (never Steam itself). Lives in a file so its own command line has no patterns.
# Matches by command line AND by process name: a game whose main thread has already exited is a zombie with an EMPTY command line but live threads, which keep
# its window mapped (seen 2026-10-09: a dead RUNNER.exe left a ghost window over the next run); `kill -9` on that pid takes the whole thread group.
me=$$
for p in /proc/[0-9]*; do
    pid=${p#/proc/}; [ "$pid" = "$me" ] && continue
    c=$(tr '\0' ' ' < $p/cmdline 2>/dev/null); n=$(cat $p/comm 2>/dev/null)
    case "$c" in
        *RUNNER.exe*|*wineserver*|*srt-bwrap*|*"reaper SteamLaunch"*|*winedevice*|*"S:\\"*|c:*system32*|C:*system32*) kill -9 $pid 2>/dev/null ;;
    esac
    case "$n" in RUNNER.exe|dosbox.exe|Launcher.exe|DOOM.exe|doom.exe|wineserver|winedevice.exe|explorer.exe|services.exe|steam.exe|pv-adverb|srt-bwrap) kill -9 $pid 2>/dev/null ;; esac
done
# Native Linux x86 games run INSIDE a FEX process (Valve's FEX tool): the process takes the game's name (comm hl_linux) but its executable is FEX; found 2026-10-10 when a
# Half-Life window stayed over the Steam UI after the script above. Generic: any process whose executable is the FEX tool's (or its server) is a game or its helper.
for p in /proc/[0-9]*; do
    pid=${p#/proc/}; [ "$pid" = "$me" ] && continue
    case "$(readlink $p/exe 2>/dev/null)" in */FEX-Emu/usr/bin/FEX|*/FEX-Emu/usr/bin/FEXServer|*/FEX-Emu/usr/bin/FEXServerManager) kill -9 $pid 2>/dev/null ;; esac
done
# Any thread group whose leader has died but whose other threads still run (a zombie leader with live threads) is a leaked game: found 2026-10-10 as two defunct WA.exe whose
# audio threads had burned 57 minutes of CPU in the background for hours (WA.exe was in no name list above). Generic on purpose: no game names.
for p in /proc/[0-9]*; do
    pid=${p#/proc/}; [ "$pid" = "$me" ] && continue
    case "$(awk '{print $3}' $p/stat 2>/dev/null)" in Z) ;; *) continue;; esac
    for t in $p/task/*; do [ "${t##*/}" != "$pid" ] && { kill -9 $pid 2>/dev/null; break; }; done
done
sleep 2
n=0; for p in /proc/[0-9]*; do case "$(cat $p/comm 2>/dev/null)" in RUNNER.exe|wineserver|srt-bwrap) n=$((n+1));; esac; done
echo "leftover game processes: $n"
