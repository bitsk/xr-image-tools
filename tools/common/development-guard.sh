# Shared guard, sourced after argument parsing. No target marker is written.
development_guard() {
 case "$XR_DEVELOPMENT_BACKEND" in
  qemu) grep -qx openkylin-qemu-amd64 /etc/xr-vm-id ;;
  native)
   $native_development || { echo 'Native sessions require explicit --native-development' >&2; return 2; }
   [[ $(uname -m) == aarch64 && $(dpkg --print-architecture) == arm64 ]] || {
    echo 'Native development profile requires an aarch64/arm64 system' >&2; return 2;
   }
   local ID=
   source /etc/os-release
   [[ $ID == openkylin ]] || { echo 'Native development profile requires openKylin' >&2; return 2; }
   ;;
  *) echo 'Select the qemu or native development entry point' >&2; return 2 ;;
 esac
 [[ $(id -u) != 0 ]] || { echo 'Run as the logged-in development user' >&2; return 2; }
}
