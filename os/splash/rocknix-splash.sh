#!/bin/sh
# WispOS replacement for ROCKNIX's /usr/bin/rocknix-splash inside the initramfs (docs/WISPOS-SPLASH-DESIGN.md).
# ROCKNIX's init (load_splash) runs this synchronously and then carries on, so the real work is started DETACHED: wisp-splash forks into the background, draws the boot
# screen on /dev/fb0 and keeps animating through switch_root (its FIFO lives in /dev, which the init moves into the new root).
# Off switch: `wisp.splash=off` on the kernel command line leaves ROCKNIX's behaviour out and the console visible (development images). `wisp.dev` adds the red DEV BUILD mark.
grep -q 'wisp.splash=off' /proc/cmdline 2>/dev/null && exit 0
dev=""; grep -q 'wisp.dev' /proc/cmdline 2>/dev/null && dev="--dev"
# Never hide a failure: if nothing has ended the splash after 90 s the boot log opens by itself (docs/WISPOS-SPLASH-DESIGN.md, "Never hide failures").
exec /usr/bin/wisp-splash --daemon --fifo /dev/wisp-splash.fifo --status "Starting" --reveal-after 90 $dev
