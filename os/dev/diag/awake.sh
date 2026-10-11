#!/bin/sh
# Dev: keep the panel on for a LIMITED time, then let it blank by itself. usage: awake.sh [SECONDS=900]   (awake.sh 0 = let it blank now). Copy to /storage/wispos/dev/.
# Why: screenshots (`grim` cannot capture a blanked output) and watching a test need the panel on, but `wisp-screen inhibit on` stays on until someone turns it off, and a test that ends
# or dies while the operator is away then burns the backlight for hours (operator, 2026-10-10: "make sure the screen turns off when the device ui is inactive ... it's not just burning
# through my backlight"). This helper sets the inhibit AND schedules its removal with a transient systemd timer (unit wisp-awake-expire), so the panel always returns to the
# product's idle blanking (wisp-screen-idle, default 120 s without a press) after SECONDS, even when the calling script is killed. A new call replaces the previous deadline.
# Dev scripts must use this instead of a bare `wisp-screen inhibit on`.
S=${1:-900}
systemctl stop wisp-awake-expire.timer wisp-awake-expire.service 2>/dev/null
if [ "$S" -gt 0 ] 2>/dev/null; then
    /usr/bin/wisp-screen inhibit on; /usr/bin/wisp-screen wake
    systemd-run --quiet --collect --unit=wisp-awake-expire --on-active="$S" /usr/bin/wisp-screen inhibit off
    echo "awake.sh: panel held on for $S s, then it blanks by itself ($(date +%T))"
else
    /usr/bin/wisp-screen inhibit off; echo "awake.sh: inhibit off, the panel blanks after the idle timeout ($(date +%T))"
fi
