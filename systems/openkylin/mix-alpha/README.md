# A081 Alpha distribution layout

The installation consists of a writable 24 GiB ext4 image in an existing ext4 GPT partition named `osstore`, and an A081 Android-format recovery image in `recovery_b`. The boot script resolves the partition by name. A shutdown initramfs moves the backing mount out of the old root by its block device number, unmounts the inner root, detaches the loop and unmounts osstore before poweroff or reboot. The end-user instructions are in [the installation guide](https://github.com/bitsk/openkylin-xr-docs/blob/main/wiki/mix-spatial-alpha.md).

Build in a disposable ARM64 root extracted with numeric ownership, hard links and xattrs from the desktop ISO pinned in `../source.lock.json`. Stage the release packages exported by the component builder for the vr-hands, cinema and saberv profiles. Use the package receipts and compiler-header audits for the selected source commits. Keep the matching system Mesa, kbd, v4l-utils and ARM64 Vulkan loader installed.

Before APT installs the packages, set dpkg path exclusions for `/usr/share/rvvr-saberv/songs/TheFatRat_Timelapse*` and `/usr/lib/aarch64-linux-gnu/xr/monado/pose-watch`. Both require a separate distribution decision. Disable the live ISO's OSTree/GRUB kernel postinstall/postremove hooks for the writable root (dpkg statoverrides preserve that choice across package upgrades). Keep the A081 module package and verify that `dpkg --audit` is empty and `apt-get check` succeeds.

Run `prepare-session`, then `configure-root`, inside the disposable root with `MIX_IMAGE_BUILD=1`. These scripts create a model-wide XR profile and desktop account and strip installation-only state. The USB serial login uses the password chosen at installation. The account and calibration units consume only the files provisioned on that device.

Run `make-root-image SEALED_ARM64_ROOT NEW_IMAGE` as root in an isolated build environment. Use e2fsprogs with extended attribute support. Compress the finished image with zstd. The raw ext4 file must remain unchanged after generating the release checksums.

Build the ramdisk with `ramdisk.py --help`. Use the locked vendor firmware cache and the kernel's `gen_init_cpio`. Package it with the matching kernel Image, source DTB and the A081 transport template using `xr-kernel/packaging/build-recovery.py`. Verify the packer's readback receipt and publish the full partition-sized recovery. Ship the two image names declared in `install-mix.py`, the installer and SHA256SUMS. Never include an Android backup or per-device calibration in the release directory.
