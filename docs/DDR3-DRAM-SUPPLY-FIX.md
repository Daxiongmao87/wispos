# DDR3 Linux DRAM supply correction

The operator reports boot still fails after recovery. Card inspection found
expected boot files and exact pre-BOOT prefix/MBR, but no boot-stage marker.
That absence does not prove a particular boot failure stage.

Inspecting the complete official 20261001 DDR3 image revealed its extlinux
FDTOVERLAYS entry for `sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo`. The overlay
sets `reg_dcdc3` minimum/maximum to 1,200,000 microvolts. Our actual flashed
DTB inherited 1,100,000 for that regulator, named `vdd-dram`. DDR3 U-Boot's
source configuration sets DCDC3 to 1,200 mV. These are measured file contents,
not measurements of the physical PMIC rail.

Kernel regulator registration applies nonzero voltage constraints and can
lower a rail above its maximum. Always-on does not prevent voltage adjustment.
This establishes a configuration inconsistency and a mechanism to reprogram
the supply; it does not establish the actual failure cause without hardware
results. Apply the official DDR3 constraints to both panel source variants.

## Verification

Extracted the original RG34XXSP DTB and DDR3 overlay from the official image,
applied the overlay using native fdtoverlay, and compared its two constraints
with our corrected DTB. Both equal 1,200,000. Native dtc comparison of our prior
and corrected diagnostic DTBs shows only those two property values changed.
Both corrected source DTS variants preprocess and compile; the new build gate
rejects the prior compiled 1.1 V DTB and accepts the corrected 1.2 V ones.

The corrected BOOT partition contains the normal embedded-initramfs kernel
with panel firmware, restored extlinux startup, and the corrected DDR3 DTB.
This test uses the original panel selection; physical panel version is not
established. Kernel SHA-256 remains
`f218319ce0b716e13dfb61e1474d7191ed8ed7d9baf47f9945aa1bfe0d8dbdc2`.
Corrected DTB SHA-256:
`e6b2089b94f7c1525213a58eccd2f90a68032b7ede9d30367982bb47aea4fa90`.
Complete BOOT SHA-256:
`58e8acf5011fa669de0f64e298a878d5ef67c750c6edd6d7154eac93b9bfa396`.

The whole official DDR3 compressed image was downloaded as a reference and
verified against its release asset digest
`222b1f8172ab5b4fef6b4b23c0482a474789cf83542a6d2e5f45dfbbf2ae1359`.
It was not flashed or presented as WispOS.

## Power-on test

Start the device with USB disconnected, using the physical power button.
Connect USB after startup for monitoring. The exact official DDR3 SPL binary
includes a charge-only shutdown path for VBUS-only power-on; this is a source
and binary observation, not a diagnosis of the preceding hardware trial.

The complete corrected BOOT partition was written to the identified card,
and direct media readback matched its hash exactly. All three card partitions
were unmounted afterward. Physical boot and regulator voltage remain unverified.

## Official sources

- [DDR3 overlay](https://github.com/ROCKNIX/distribution/blob/20261001/projects/ROCKNIX/packages/kernel-drivers/device-tree-overlays/sources/H700/sun50i-h700-anbernic-rg35xx-2024-ddr3.dts)
- [DDR3 U-Boot configuration](https://github.com/ROCKNIX/distribution/blob/20261001/projects/ROCKNIX/devices/H700/packages/u-boot-DDR3/sources/configs/anbernic_rg35xx_h700_lpddr3_defconfig)
- [Runtime DDR3/DDR4 bootloader selection](https://github.com/ROCKNIX/distribution/blob/20261001/projects/ROCKNIX/devices/H700/bootloader/update.sh)
