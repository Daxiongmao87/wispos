# Alternate DDR3 bootloader test

The operator reports that the first native Steam test image does not boot and
shows no light on the 1 GiB RG34XXSP. The operator requested the other bootloader
variant. The required project bootloader variant is now DDR3. The DRAM package itself
has not been identified by a hardware inventory.

Direct comparison confirmed that the original image's first 16 MiB matches
`bootloader/known-good-ddr4-prefix.bin`, excluding the rewritten MBR table.
The DDR4 designation comes from that prefix's recorded provenance; it was not
physically validated on this unit. The repository's "known-good" label does
not establish that it boots the operator's revision.

[Anbernic's official firmware page](https://win.anbernic.com/download/586.html)
links a folder containing `RG34XXSP-V1.0.6-EN16GB-260526.IMG.7z`. The listing
does not distinguish DDR3/DDR4 downloads. This archive was not downloaded or
inspected, so its internal RAM support remains unverified.

[ROCKNIX's 20261001 release](https://github.com/ROCKNIX/distribution/releases/tag/20261001)
lists RG34XXSP under both H700 DDR4 and DDR3 images. The alternate image uses
the bootloader prefix from its DDR3 image. The full 1,256,162,325-byte compressed
download matched GitHub's published SHA-256 and passed gzip integrity checking.
Only its first 16 MiB was retained. Source URL and hashes are pinned in
`bootloader/experimental-ddr3.lock.json`.

## Exact change

`build/steam34-native-steam-test-ddr3.img` is derived from the first native
Steam test image. It replaces bytes **8192 through 16777215**, inclusive, with
the DDR3 source prefix. It preserves bytes 0–8191, including the partition
table, and every byte from 16 MiB onward. Kernel, DTB, initramfs, SYSTEM and DATA
are therefore unchanged. The bootloader release also differs from the older
prefix; this is an alternate boot chain test, not an isolated DRAM-code patch.

The regular-file assembler refuses existing outputs, validates sunxi SPL magic
and the prefix hash, checks partition geometry, compares all bytes outside the
changed range, and records input/output hashes in a JSON sidecar. No mounts,
host package changes, foreign execution or flashing occur.

## Reproduce

Download the DDR3 asset linked in the lock and verify its full compressed hash.
Extract its prefix from that verified download without mounting it:

```python
import gzip
from pathlib import Path
with gzip.open('ROCKNIX-H700.aarch64-20261001-DDR3.img.gz', 'rb') as stream:
    Path('build/rocknix-20261001-ddr3-prefix.bin').write_bytes(stream.read(16777216))
```

Then assemble a fresh output:

```sh
python3 -B tools/assemble-boot-variant.py \
  --base build/steam34-native-steam-test.img \
  --prefix build/rocknix-20261001-ddr3-prefix.bin \
  --prefix-sha256 da936e70163f1767f15b7960003cab94b89e20ec55b6da78f1f8046a47c6d833 \
  --output build/steam34-native-steam-test-ddr3.img
```

Run `python3 -B tools/test_assemble_boot_variant.py` for the preservation,
bad-hash and refusal-to-overwrite tests.

## Produced artifact

- Image length: 10,737,418,240 bytes (10 GiB).
- SHA-256: `87cfd7ceec368f96e473158e2cfc0fd33d849aa9843adfd1e1ce636fe96de0c6`.
- Sidecar: `build/steam34-native-steam-test-ddr3.img.json`.
- All 10,720,649,216 bytes outside the bootloader interval compared equal.
- Three assembler tests passed. No physical boot has been tested here.

## Physical decision gate

Flash this separate image to the operator-selected card and test power-on.
Record whether a light, display, console or DATA logs appear. The operator reports that the DDR3 image booted with a green light, while
waiting for display output. Linux and Steam startup are not yet confirmed. If it remains dark, that result alone does not
identify the next failure; inspect the boot chain and device revision before
changing Steam or Proton. Follow `docs/NATIVE-TEST-IMAGE.md` if boot progresses.

## Device report: 2026-10-03

The operator reported: "that one booted, light is green" for the DDR3 image.
DDR3 is now the required project bootloader choice, and both appliance
assembly scripts select `bootloader/required-ddr3-prefix.bin` with its hash
gate. The former DDR4 prefix is retained as historical provenance only.
The running card has not been rewritten by this documentation/config update.

First boot copies roughly 932 MiB of client files before starting Xorg. No
copy duration has been measured on this unit. Preparation uses systemd's
default startup timeout; slow copying could cause that service to time out.
A blank display requires logs before assigning a cause.
