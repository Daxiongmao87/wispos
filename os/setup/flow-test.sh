#!/bin/bash
# End-to-end tests of wisp-setup in the emulated VM, with a fake nmcli and curl (no real Wi-Fi needed).
#
#   os/setup/flow-test.sh PATH/TO/wisp-setup(aarch64)       needs the VM: os/qemu-vm.sh start
#
# Each scenario feeds the real binary a button script, then checks its exit code, the exact sequence of screens it went
# through, and the commands it ran (what reached nmcli, and that nothing reached it through a shell).
set -u
HERE=$(cd "$(dirname "$0")" && pwd); VM="bash $HERE/../qemu-vm.sh"; BIN=${1:?path to the aarch64 wisp-setup binary}
$VM put "$BIN" /tmp/wisp-setup && $VM put "$HERE/assets/wispos-badge.png" /tmp/wispos-badge.png && $VM put "$HERE/test/fake-nmcli" /tmp/fake-nmcli && $VM put "$HERE/test/fake-curl" /tmp/fake-curl && $VM put "$HERE/test/fake-installer" /tmp/fake-installer && $VM ssh "chmod +x /tmp/fake-installer /tmp/fake-nmcli /tmp/fake-curl" || { echo "cannot reach the VM (os/qemu-vm.sh start)"; exit 2; }
fails=0; passes=0
run() {  # name, script, [pre-command]
    local name=$1 script=$2 pre=${3:-:}
    OUT=$($VM ssh "rm -rf /tmp/fs; mkdir -p /tmp/fs/shots; $pre; cd /tmp; WISP_NMCLI=/tmp/fake-nmcli WISP_CURL=/tmp/fake-curl WISP_INSTALLER=/tmp/fake-installer WISP_READY=/tmp/fs/READY FAKE_DIR=/tmp/fs SDL_VIDEODRIVER=dummy /tmp/wisp-setup --badge /tmp/wispos-badge.png ${script:+--script \"$script\"} --shots /tmp/fs/shots --timeout 60 >/tmp/fs/out.txt 2>&1; echo exit=\$? >>/tmp/fs/out.txt; cat /tmp/fs/out.txt; echo ---calls---; cat /tmp/fs/calls.log")
    STATES=$(echo "$OUT" | grep -o 'state=[a-z]*' | sed 's/state=//' | tr '\n' ' ')
}
check() {  # description, condition result
    if [ "$2" = ok ]; then passes=$((passes + 1)); echo "PASS  $1"; else fails=$((fails + 1)); echo "FAIL  $1"; echo "$OUT" | sed 's/^/        /' | head -30; fi
}
has() { echo "$OUT" | grep -qF -- "$1" && echo ok || echo no; }
hasnt() { echo "$OUT" | grep -qF -- "$1" && echo no || echo ok; }
states_are() { [ "$STATES" = "$1 " ] && echo ok || { echo "        states were: $STATES"; echo no; }; }

run happy "until:networks a type:correct-horse-battery start until:done"
check "right password: list, password, connecting, checking, done; exit 0" "$([ "$(states_are 'list password connecting checking download done')" = ok ] && [ "$(has 'exit=0')" = ok ] && echo ok)"
check "the password and network name reached nmcli as separate arguments" "$(has 'device wifi connect Home-WiFi-5G password correct-horse-battery')"
check "Steam's manifest was what got checked" "$(has 'steam_client_publicbeta_linuxarm64')"
$VM get /tmp/fs/shots/00-list.bmp /mnt/hdd/wispos-staging/wispos-os-20261007/tmp/ui/setup/flow-list.bmp 2>/dev/null

run wrongthenright "until:networks a type:wrongpassword1 start until:failed a type:correct-horse-battery start until:done"
check "wrong password fails with a reason, 'Try again' reopens the password, then it works" "$([ "$(states_are 'list password connecting failed password connecting checking download done')" = ok ] && [ "$(has 'exit=0')" = ok ] && echo ok)"
check "the bad saved profile is deleted after a failure" "$(has 'connection delete id Home-WiFi-5G')"

run open "until:networks down down a until:done"
check "an open network connects with no password prompt and no password argument" "$([ "$(states_are 'list connecting checking download done')" = ok ] && [ "$(hasnt 'password')" = ok ] && echo ok)"

run captive "until:networks down down down a until:nointernet down a until:list quit"
check "a captive portal joins but fails the Steam check, 'Choose another network' returns to the list" "$(states_are 'list connecting checking nointernet list')"

run online "" "echo 'Home-WiFi-5G' >/tmp/fs/connected"
check "online but Steam not installed: no Wi-Fi screen, straight to the download, exits 0 when it is installed" "$([ "$(states_are 'download done')" = ok ] && [ "$(has 'exit=0')" = ok ] && [ "$(hasnt 'device wifi connect')" = ok ] && [ "$(has 'installer run')" = ok ] && echo ok)"

run installed "" "echo 'Home-WiFi-5G' >/tmp/fs/connected; echo Ready >/tmp/fs/READY"
check "already installed: exits 0 at once, no screen, no installer, no network check" "$([ "$(has 'already installed')" = ok ] && [ "$(has 'exit=0')" = ok ] && [ "$(hasnt 'state=')" = ok ] && [ "$(hasnt 'installer run')" = ok ] && [ "$(hasnt 'curl')" = ok ] && echo ok)"

run dlretry "until:networks a type:correct-horse-battery start until:dlfailed a until:done" "echo failonce >/tmp/fs/installer"
check "a failed download shows its own screen; 'Try again' runs the installer again and finishes" "$([ "$(states_are 'list password connecting checking download dlfailed download done')" = ok ] && [ "$(echo "$OUT" | grep -c 'installer run')" = 2 ] && [ "$(has 'exit=0')" = ok ] && echo ok)"

run dlother "until:networks a type:correct-horse-battery start until:dlfailed down a until:list quit" "echo fail >/tmp/fs/installer"
check "after a failed download 'Choose another network' returns to the list" "$(states_are 'list password connecting checking download dlfailed list')"

run noready "until:networks a type:correct-horse-battery start until:dlfailed quit" "echo noready >/tmp/fs/installer"
check "an installer that says done but left no READY is a failure, not a success" "$([ "$(states_are 'list password connecting checking download dlfailed')" = ok ] && [ "$(hasnt 'state=done')" = ok ] && echo ok)"

run noinstaller "until:networks a type:correct-horse-battery start until:dlfailed quit" "echo missing >/tmp/fs/installer"
check "a missing installer ends in the failure screen, not a hang or a crash" "$(states_are 'list password connecting checking download dlfailed')"

run short "until:networks a type:short start dump quit"
check "a password under 8 characters is refused with a message and never sent" "$([ "$(states_are 'list password')" = ok ] && [ "$(hasnt 'device wifi connect')" = ok ] && echo ok)"

run hidden "until:networks y type:Secret_Net start type:hunter2hunter2 start until:done"
check "hidden network: name first, then password, joined with 'hidden yes'" "$([ "$(has 'connect Secret Net password hunter2hunter2 hidden yes')" = ok ] && [ "$(has 'exit=0')" = ok ] && echo ok)"

run noadapter "quit" "export FAKE_NO_WIFI=1"
check "no Wi-Fi adapter shows its own screen" "$(states_are 'noadapter')"

run osk "until:networks a a right right a down a dump x a dump quit"
check "on-screen keyboard: moving and typing gives 'qes', one-shot shift adds 'S' and releases" "$([ "$(has 'pw=[qes]')" = ok ] && [ "$(has 'pw=[qesS] ssid=[Home-WiFi-5G] shift=0')" = ok ] && echo ok)"

run backout "until:networks a type:abc b b b b quit"
check "B deletes characters, then goes back to the list when empty" "$(states_are 'list password list')"

# Real input path: a virtual gamepad (the dev tool) feeding the real program through SDL, the way a handheld's pad does.
$VM put "$HERE/../rootfs-dev/usr/bin/wisp-dev-pad" /tmp/wisp-dev-pad >/dev/null
run_pad() {  # name, presses
    OUT=$($VM ssh "rm -rf /tmp/fs; mkdir -p /tmp/fs; cd /tmp; python3 /tmp/wisp-dev-pad start >/dev/null; WISP_NMCLI=/tmp/fake-nmcli WISP_CURL=/tmp/fake-curl WISP_INSTALLER=/tmp/fake-installer WISP_READY=/tmp/fs/READY FAKE_DIR=/tmp/fs SDL_VIDEODRIVER=dummy /tmp/wisp-setup --badge /tmp/wispos-badge.png --timeout 90 >/tmp/fs/out.txt 2>&1 & sleep 14; python3 /tmp/wisp-dev-pad press $2; sleep 3; python3 /tmp/wisp-dev-pad stop >/dev/null; for p in \$(ps | grep '[w]isp-setup' | awk '{print \$1}'); do kill -9 \$p; done; cat /tmp/fs/out.txt")
    STATES=$(echo "$OUT" | grep -o 'state=[a-z]*' | sed 's/state=//' | tr '\n' ' ')
}
run_pad padone "down a"
check "one D-pad press from a real pad moves the cursor exactly one row (second network is secured: password)" "$(states_are 'list password')"
run_pad padopen "down down a"
check "two D-pad presses reach the third network (open: connects without a password)" "$([ "$(states_are 'list connecting checking download done')" = ok ] && echo ok)"

echo; echo "$passes passed, $fails failed"; [ $fails -eq 0 ]
