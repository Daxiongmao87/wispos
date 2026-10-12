#!/bin/bash
# Prove that the build is reproducible: build the same commit twice, from clean checkouts in two DIFFERENT directories, in parallel, and compare every artifact byte for byte
# (docs/WISPOS-RELEASING.md, step 4). Two directories because a build that embeds its own path (a compiler's __FILE__, a debug section, a tool's log line) only shows itself that way.
#
#   os/repro-check.sh [--seed WORKDIR] [--against WORKDIR] [--dirty] [--keep]
#
#   --seed WORKDIR     where the pinned INPUTS come from (default /mnt/hdd/wispos-staging/wispos-os-20261007): dl (hash-checked downloads), rocknix-src, prefix, ref-boot and sdbus-inc are
#                      copied into each build's own work directory (reflinks on the Btrfs HDD: instant) so the builds only read their copies and never the seed. They are inputs, not
#                      results: every one is checked against its pin when it is used. Everything else is built from scratch by os/build-all.sh.
#   --against WORKDIR  also compare with the artifacts already in WORKDIR/out (the candidate that was tested on the device). They match only when the commit differs from the
#                      candidate in files that are not image inputs (the changelog, docs, the tag).
#   --dirty            build a snapshot of the working tree (tracked and untracked files) instead of HEAD, for a dry run before the release commit exists. The result says it is no proof.
#   --keep             keep the two work directories after success (they are kept after a mismatch anyway, to look at).
# The dev image is compared too when WISP_SSH_KEY and WISP_DEV_ANNOUNCE are set (os/build-all.sh). Exit 0 only when every artifact of A equals B (and --against).
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
SEED=/mnt/hdd/wispos-staging/wispos-os-20261007; AGAINST=; DIRTY=; KEEP=
while [ $# -gt 0 ]; do
    case $1 in
        --seed) SEED=$2; shift ;; --against) AGAINST=$2; shift ;; --dirty) DIRTY=1 ;; --keep) KEEP=1 ;;
        *) echo "repro-check: unknown option $1" >&2; exit 2 ;;
    esac; shift
done
mountpoint -q /mnt/hdd || { echo "repro-check: /mnt/hdd is not mounted" >&2; exit 2; }
for p in "$SEED" ${AGAINST:+"$AGAINST"}; do case $(realpath -m "$p") in /mnt/hdd/*) ;; *) echo "repro-check: $p must be on the HDD" >&2; exit 2 ;; esac; done
[ -d "$SEED" ] || { echo "repro-check: no seed directory $SEED" >&2; exit 2; }
if [ -z "$DIRTY" ] && [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    echo "repro-check: the working tree is not clean; commit (or use --dirty for a dry run that proves nothing about a commit)" >&2; exit 2
fi
REPRO=$SEED/repro; RUN=$REPRO/run-$(date -u +%Y%m%d-%H%M%S)-$$
case $RUN in /mnt/hdd/*/repro/run-*) ;; *) echo "repro-check: refusing odd path $RUN" >&2; exit 2 ;; esac
mkdir -p "$RUN"
COMMIT=$(git -C "$ROOT" rev-parse --short=12 HEAD)
echo "repro-check: ${DIRTY:+SNAPSHOT OF A DIRTY TREE based on }commit $COMMIT, builds in $RUN/{a,b}"

prepare() {  # NAME: a checkout of the commit and the pinned inputs, in $RUN/NAME
    local d=$RUN/$1; mkdir -p "$d/src" "$d/work"
    if [ -n "$DIRTY" ]; then
        (cd "$ROOT" && git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' f; do [ -e "$f" ] && printf '%s\0' "$f"; done | tar --null -T - -cf -) | tar -xf - -C "$d/src"
    else
        git -C "$ROOT" archive HEAD | tar -xf - -C "$d/src"
    fi
    for input in dl rocknix-src prefix ref-boot sdbus-inc; do
        if [ -e "$SEED/$input" ]; then cp -a --reflink=auto "$SEED/$input" "$d/work/$input"; else echo "repro-check: note: no $input in the seed; build $1 will fetch or make it" >&2; fi
    done
}
for n in a b; do prepare $n; done

declare -A PID
for n in a b; do
    ( cd "$RUN/$n/src" && exec bash os/build-all.sh "$RUN/$n/work" ) >"$RUN/$n.log" 2>&1 &
    PID[$n]=$!
done
echo "repro-check: building A (pid ${PID[a]}) and B (pid ${PID[b]}); logs $RUN/a.log, $RUN/b.log"
FAILED=
for n in a b; do wait "${PID[$n]}" || { echo "repro-check: build $n FAILED (see $RUN/$n.log, last lines:)" >&2; tail -5 "$RUN/$n.log" >&2; FAILED=1; }; done
[ -z "$FAILED" ] || exit 1

# the artifacts: A's list; B must name the same ones
cmp -s "$RUN/a/work/out/ARTIFACTS" "$RUN/b/work/out/ARTIFACTS" || { echo "repro-check: the two builds made different sets of artifacts" >&2; exit 1; }
printf '\n%-36s %-16s %-16s %s\n' artifact A B "${AGAINST:+AGAINST}"
BAD=0
while read -r name; do
    ha=$(sha256sum "$RUN/a/work/out/$name" | cut -c1-64); hb=$(sha256sum "$RUN/b/work/out/$name" | cut -c1-64); hc=
    verdict=identical
    [ "$ha" = "$hb" ] || { verdict=DIFFERENT; BAD=1; }
    if [ -n "$AGAINST" ]; then
        if [ -f "$AGAINST/out/$name" ]; then hc=$(sha256sum "$AGAINST/out/$name" | cut -c1-64); else hc=missing; fi
        [ "$hc" = "$ha" ] || { verdict="$verdict, NOT the candidate"; BAD=1; }
    fi
    printf '%-36s %-16s %-16s %-16s %s\n' "$name" "${ha:0:16}" "${hb:0:16}" "${hc:0:16}" "$verdict"
done <"$RUN/a/work/out/ARTIFACTS"
if [ "$BAD" != 0 ]; then
    echo; echo "repro-check: MISMATCH. The work directories are kept in $RUN (compare with: cmp -l A B | head). A result that depends on the directory shows up in the paths of a/ and b/." >&2; exit 1
fi
echo; echo "repro-check: REPRODUCIBLE: every artifact of A equals B${AGAINST:+ and $AGAINST}."
if [ -z "$KEEP" ]; then rm -rf -- "$RUN"; else echo "repro-check: kept $RUN"; fi
[ -z "$DIRTY" ] || echo "repro-check: (this was a snapshot of a dirty tree: it says the BUILD is reproducible, not that any commit is)"
