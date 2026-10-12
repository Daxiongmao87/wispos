# Releasing WispOS

What a release is (project `AGENTS.md`, "Versioning and changelog"): **the version in `manifest/lockfile.json`**, **an annotated tag `vX.Y.Z`**, and **the `CHANGELOG.md` section**. A dirty working tree is never a released state, and a commit hash is not a version.

## The rule that keeps "tested" equal to "shipped"
The image carries its version and its build id (stamped from the lockfile by `os/stage_rootfs.py` into `/etc/os-release` as `WISPOS_VERSION`, `VERSION_ID`, `BUILD_ID`), but not the changelog and not the tag. So a **release candidate** built at commit C is byte-identical to the **release** built at the tagged commit R whenever R differs from C only in files that are not image inputs (`CHANGELOG.md`, `docs/`, the tag itself). The order below is therefore: build at C, let the operator test that exact image on the device, and only then cut R and tag it. `os/repro-check.sh` proves the equality by rebuilding from a clean checkout of R and comparing hashes.

## Files and who changes them
| what | where | changed by |
|---|---|---|
| version (SemVer) and build id (`YYYYMMDD.N`) | `manifest/lockfile.json` | the person cutting a candidate; nowhere else (the image, the update manifest and `os/make-release.py` all read it) |
| human ledger | `CHANGELOG.md` (`[Unreleased]` until tagging) | every change that alters behaviour adds an entry naming its SemVer class |
| update signing key | public half `os/steamos/update-key.pub` (in every image), private half `~/.wispos-release/update-signing.pem` (never in the repository, mode 0600, back it up) | `os/make-release.py keygen` once |

## Steps
1. **Candidate.** Set `version` and `build_id` in `manifest/lockfile.json` (N counts the candidates of that day), commit. Build everything with `os/build-all.sh` (bootloader, initramfs, kernel, both SYSTEMs, both images in dependency order, the release steps without any development knob; the dev image needs `WISP_SSH_KEY` and `WISP_DEV_ANNOUNCE`; it writes `out/ARTIFACTS.sha256`); run `os/verify_image.py` and `os/check_rootfs.py` on them, then the boot tests (`os/qemu-test.sh`, `os/qemu-update-test.sh`) and the whole host suite (`os/run-tests.py`). Record the image hashes (docs/WISPOS-NOTES.md, "Device package built ...").
2. **Device.** The operator flashes and runs the checklist (docs/WISPOS-NOTES.md, "Next device session"). Nobody else flashes: an agent never does.
3. **If it fails:** fix it, raise `build_id` (`.2`), repeat from 1. The version string stays.
4. **If it passes:** cut the release commit R: in `CHANGELOG.md` rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD` and add a new empty `[Unreleased]`; commit `release: X.Y.Z`. Run `os/repro-check.sh --against WORKDIR` on R (clean tree required): `git archive HEAD` is built twice by `os/build-all.sh`, in parallel, in two work directories of different names (pinned inputs copied from the seed: downloads, the ROCKNIX checkout, the reference initramfs); every artifact (bootloader, kernel Image, SYSTEM and image, dev ones when the dev inputs are set) must equal the other build's and the candidate's in WORKDIR/out. A mismatch keeps both directories. `--dirty` does the same for a snapshot of the working tree before R exists (it proves the BUILD is reproducible, not a commit).
5. **Tag.** `git tag -a vX.Y.Z -m "WispOS X.Y.Z (build YYYYMMDD.N)"` on R. A tag is never moved or deleted; a bad release is superseded by a new version.
6. **Update assets** (for devices already running an earlier release): `os/make-release.py build --kernel out/kernel/Image --system out/SYSTEM --base-url https://github.com/OWNER/REPO/releases/download/vX.Y.Z --out DIR` (version and build id default to the lockfile and the SYSTEM must say the same, or the tool refuses). It writes `KERNEL`, `SYSTEM`, `manifest.json`, `manifest.json.sig`, `RELEASE.txt`. **Publishing them as a GitHub Release is the operator's act.** The release gate (`os/check_release.py --strict`) runs inside the tool; `--allow-gaps` overrides it and says so in `RELEASE.txt`.
7. **Public tree.** `python3 tools/make-public.py` regenerates `public/` (its own git repository) from the whitelist and scans it for leaks; run the tests there (`python3 -I os/test_*.py`); commit in `public/`. **Pushing is the operator's act.**

## What is NOT part of the version
The device tree, the overlay and the kernel command line are shared by both boot slots and are not carried by an update (docs/WISPOS-UPDATE-DESIGN.md): a change to them is a re-flash release and must say so in its changelog entry and in the release notes.

## Release notes
`docs/WISPOS-RELEASE-NOTES-X.Y.Z.md` is written at step 1 for the candidate (what is in, what is not, what was only tested on the host, how to flash, how to get back) and finished at step 4.
