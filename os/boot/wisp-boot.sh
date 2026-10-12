# /wisp-boot: WispOS's addition to ROCKNIX's initramfs, the boot half of the two-slot update scheme (docs/WISPOS-UPDATE-DESIGN.md, "Boot slots").
# os/boot/patch_initramfs.py puts this file at /wisp-boot, adds one line to /init that sources it, and adds one boot step, wisp_slot, between cleanup_flash and
# mount_sysroot (after the boot partition is mounted, before the system image is). Plain POSIX shell for the initramfs's busybox ash; no bashisms.
#
# The card holds two complete systems, slots a and b (a: /KERNEL and /SYSTEM, b: /wispos/b/KERNEL and /wispos/b/SYSTEM). U-Boot starts the one named by the FIRST LINE of
# /extlinux/extlinux.conf, "DEFAULT a" or "DEFAULT b" (a one-letter change in place: no file is ever renamed, so at every instant there is a kernel to load), and that
# label's kernel command line says which one it is (wisp.slot=a|b). An update (os/steamos/wisp-update) is written into the slot that is NOT running, verified, and only then
# switched: it writes /wispos/update/PENDING (slot, build_id, version, tries=0) and flips the letter. This step is what makes the switch safe:
#   - no PENDING: nothing to do. PENDING for the other slot: the switch was never made (power cut) or has been undone: the marker is stale and is removed.
#   - PENDING for THIS slot: the new version is on trial. Each start counts (tries=1, 2); the running system deletes PENDING once it has come up well (wisp-update confirm).
#     A start that finds the count already at WISP_TRIES means the version did not come up well twice: the card goes back to the other slot (flip the letter, leave
#     FAILED-<build_id> so the build is not offered again, delete PENDING) and restarts, so the previous version's own kernel boots with its own system.
#   - WHILE A VERSION IS ON TRIAL a failing initramfs step must not stop the start: the reference init ends such a step in a debug shell nobody can type into ("Could not find
#     systemd!", a system image that does not mount, ...) or in a power-off that needs a finger on Power, so the trial would only ever advance when someone power-cycled the
#     handheld. Once the start is counted (durably), `error` and `poweroff` are replaced by a restart (wisp_trial_guard): three starts later the card is back on the previous
#     version without anyone touching the device. Outside a trial the reference behaviour is untouched.
#   - A KERNEL THAT NEVER REACHES THIS STEP is counted by U-Boot (os/boot/uboot/wisp-trial.txt): it adds one to wispos/update/BOOTS at every start of the slot PENDING names and
#     starts the previous version from extlinux/rollback.conf when the count is already 2. This step deletes BOOTS as soon as the trial slot gets here, and, when the letter
#     names the version on trial while THIS slot is the one running (U-Boot could not load that kernel, or counted it out), completes the bookkeeping: FAILED-<id>, the letter
#     back to this slot, PENDING and BOOTS deleted.
# The slot decides which system image the init mounts (IMAGE_SYSTEM). A kernel that never starts cannot run this: U-Boot falls back to the other label only when it cannot
# LOAD a kernel (boot/pxe_utils.c boot_unattempted_labels); a kernel that loads and then dies before this step is counted by U-Boot itself (see BOOTS above) and, after two such
# starts, replaced by the previous version; nothing restarts a dead kernel, so the person power-cycles the handheld in between.
#
# Test seams (the init sets none of them): WISP_FLASH (/flash), WISP_CMDLINE (/proc/cmdline), WISP_KMSG (/dev/kmsg), WISP_TRIES (2), WISP_TRIAL_PAUSE (3 s: time to read the
# message before the restart).

WISP_FLASH=${WISP_FLASH:-/flash}
WISP_CMDLINE=${WISP_CMDLINE:-/proc/cmdline}
WISP_KMSG=${WISP_KMSG:-/dev/kmsg}
WISP_TRIES=${WISP_TRIES:-2}

wisp_say() { echo "wispos: $*"; echo "wispos: $*" >"$WISP_KMSG" 2>/dev/null; return 0; }
wisp_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | head -n 1; }                  # the value of KEY in a KEY=value file
wisp_other() { if [ "$1" = a ]; then echo b; else echo a; fi; }
wisp_kernel_of() { if [ "$1" = a ]; then echo "$WISP_FLASH/KERNEL"; else echo "$WISP_FLASH/wispos/b/KERNEL"; fi; }
wisp_system_of() { if [ "$1" = a ]; then echo "$WISP_FLASH/SYSTEM"; else echo "$WISP_FLASH/wispos/b/SYSTEM"; fi; }
wisp_complete() { [ -s "$(wisp_kernel_of "$1")" ] && [ -s "$(wisp_system_of "$1")" ]; }       # a slot a start can be made from: both files there and not empty

wisp_cmdline_slot() {                                                                           # the slot this boot was started as; none given (an older card): a
    local arg
    for arg in $(cat "$WISP_CMDLINE" 2>/dev/null); do
        case $arg in wisp.slot=a|wisp.slot=b) echo "${arg#wisp.slot=}"; return 0 ;; esac
    done
    echo a
}

wisp_default() {                                                                                # the slot U-Boot starts: line 1 of extlinux.conf must be exactly "DEFAULT a" or "DEFAULT b"
    case $(head -n 1 "$WISP_FLASH/extlinux/extlinux.conf" 2>/dev/null) in
        'DEFAULT a') echo a ;; 'DEFAULT b') echo b ;; *) return 1 ;;
    esac
}

wisp_set_default() {                                                                            # flip the letter in place: one byte of one sector, nothing renamed, nothing truncated
    wisp_default >/dev/null || return 1
    printf '%s' "$1" | dd of="$WISP_FLASH/extlinux/extlinux.conf" bs=1 seek=8 count=1 conv=notrunc 2>/dev/null || return 1
    sync
    [ "$(wisp_default)" = "$1" ]
}

wisp_rw() { mount -o remount,rw "$WISP_FLASH" 2>/dev/null; }
wisp_ro() { sync; mount -o remount,ro "$WISP_FLASH" 2>/dev/null || wisp_say "warning: the boot partition could not be made read-only again"; return 0; }

wisp_switch_back() {                                                                            # wisp_switch_back BAD_SLOT GOOD_SLOT BUILD_ID: the failure marker, the letter, the markers gone (no restart); 1 and nothing written when it cannot
    local bad=$1 good=$2 build=$3
    wisp_default >/dev/null || { wisp_say "cannot go back: extlinux.conf is not in the expected form"; return 1; }       # before anything is written
    wisp_rw || { wisp_say "cannot go back: the boot partition will not become writable"; return 1; }
    case $build in ''|*[!0-9.]*) build=unknown ;; esac                                          # it becomes the name of a file on the card: digits and dots only
    printf 'slot=%s\n' "$bad" >"$WISP_FLASH/wispos/update/FAILED-$build" 2>/dev/null           # first: so that, whatever happens next, the build is not offered again
    sync
    if ! wisp_set_default "$good"; then wisp_say "cannot go back: extlinux.conf is not in the expected form"; wisp_ro; return 1; fi
    rm -f "$WISP_FLASH/wispos/update/PENDING"
    rm -f "$WISP_FLASH/wispos/update/BOOTS"
    wisp_ro
    return 0
}

wisp_rollback() {                                                                               # wisp_rollback BAD_SLOT BUILD_ID: go back to the other slot, then restart (does not return when it works)
    local bad=$1 build=$2 good
    good=$(wisp_other "$bad")
    if ! wisp_complete "$good"; then wisp_say "version $build did not start well, and there is nothing to go back to (slot $good is incomplete)"; return 1; fi
    wisp_switch_back "$bad" "$good" "$build" || return 1
    wisp_say "version $build did not start well: going back to the previous version (slot $good), restarting"
    reboot -f
    return 0
}

wisp_trial_failed() { wisp_say "start $WISP_TRIAL_N of $WISP_TRIES on trial failed ($*): restarting"; [ "${WISP_TRIAL_PAUSE:-3}" = 0 ] || sleep "${WISP_TRIAL_PAUSE:-3}"; reboot -f; }
wisp_trial_guard() {                                                                            # wisp_trial_guard N: from now on this start (number N of the trial) restarts instead of stopping
    WISP_TRIAL_N=$1
    error() { echo "*** Error in ${BOOT_STEP}: $1: $2 ***" >&2; wisp_trial_failed "$1: $2"; return 1; }
    poweroff() { wisp_trial_failed "power off requested"; }
}

wisp_slot() {                                                                                   # the boot step
    local slot pend p_slot p_build p_tries n
    slot=$(wisp_cmdline_slot)
    [ "$slot" = b ] && IMAGE_SYSTEM=wispos/b/SYSTEM                                             # mount_sysroot reads this (an init variable, relative to /flash); slot a keeps the init's own SYSTEM
    pend=$WISP_FLASH/wispos/update/PENDING
    [ -f "$pend" ] || return 0
    p_slot=$(wisp_value "$pend" slot); p_build=$(wisp_value "$pend" build_id); p_tries=$(wisp_value "$pend" tries)
    case $p_slot in a|b) ;; *) wisp_say "the update marker is unreadable: left alone"; return 0 ;; esac
    if [ "$p_slot" != "$slot" ]; then
        if [ "$(wisp_default)" = "$p_slot" ]; then                                              # the letter names the version on trial and this is the OTHER slot: it never started
            wisp_say "version $p_build never started (the bootloader started slot $slot instead of slot $p_slot): going back to slot $slot for good"
            wisp_switch_back "$p_slot" "$slot" "$p_build"
            return 0
        fi
        wisp_say "stale update marker for slot $p_slot (running slot $slot): removed"
        wisp_rw && rm -f "$pend" "$WISP_FLASH/wispos/update/BOOTS"
        wisp_ro
        return 0
    fi
    case $p_tries in ''|*[!0-9]*) p_tries=0 ;; esac
    if [ "$p_tries" -ge "$WISP_TRIES" ]; then
        wisp_say "version $p_build was started $p_tries times and never came up well"
        wisp_rollback "$slot" "$p_build"
        return 0                                                                                # only reached when going back was not possible: carry on with what is here
    fi
    n=$((p_tries + 1))
    wisp_say "starting version $p_build on trial ($n of $WISP_TRIES)"
    wisp_rw || return 0
    if grep -q '^tries=' "$pend" 2>/dev/null; then sed "s/^tries=.*/tries=$n/" "$pend" >"$pend.tmp"; else { cat "$pend"; echo "tries=$n"; } >"$pend.tmp"; fi
    rm -f "$WISP_FLASH/wispos/update/BOOTS"                                                     # this kernel got here: the bootloader's count starts again
    mv -f "$pend.tmp" "$pend" || { wisp_ro; return 0; }                                         # no durable count: no guard (a restart loop nobody counts would never end)
    wisp_ro
    wisp_trial_guard "$n"
    return 0
}
