"""Builds a flashable Magisk/KernelSU module (.zip) from a patched APK.

HOW THIS WORKS (please read before relying on it):

Android's package manager only verifies an APK's signature at install time,
not on every subsequent launch -- once a package has been genuinely
installed and registered, the OS trusts whatever bytes are on disk at that
path afterwards. So this module:

  1. Ships a full copy of the STOCK (unpatched) app's split APKs, and force
     -installs them via a real `pm install-create` / `install-write` /
     `install-commit` session if the exact patched version isn't already
     installed. A session install can create, upgrade, *or downgrade* a
     package, which is what lets this work with no prior install of the
     app, or a different version installed -- not just an exact match.
  2. Bind-mounts the separately-patched APK directly over that
     now-genuinely-installed copy's base.apk file. This is a filesystem
     swap that happens *after* Android already trusted and registered the
     real, validly-signed stock app, so it doesn't re-trigger signature
     verification.
  3. Re-applies the mount at every boot (service.sh), since bind mounts
     don't survive a reboot on their own.

This is the same well-established, widely-used community technique found in
tools like j-hc/revanced-magisk-module (https://github.com/j-hc/revanced-magisk-module),
which is GPLv3-licensed -- the shell scripts below are an independent,
from-scratch implementation of the same *technique* (which is just standard,
publicly documented Android `pm` session usage), not a copy of that
project's code, so this repository's own license terms are unaffected.
If you want the exact original implementation (e.g. its KernelSU unmount-
detection helper), that project is the place to get it, under its own
GPLv3 terms.

CAVEATS:
  * Requires root (Magisk or KernelSU).
  * This cannot be exercised against a real rooted device inside a CI
    sandbox -- treat build_mode "module"/"both" as experimental and test on
    a real device before relying on it daily.
"""

import os
import time
import zipfile

from config import AppConfig

# .apkm bundles (see https://en.wikipedia.org/wiki/Android_App_Bundle /
# Android's bundletool) name their native-library splits using these exact
# tokens, e.g. "split_config.arm64_v8a.apk". Verified against real APKMirror
# bundle naming conventions.
ARCH_SPLIT_KEYWORDS = {
    "arm64-v8a": "arm64_v8a",
    "armeabi-v7a": "armeabi_v7a",
    "x86_64": "x86_64",
    "x86": "x86",
}


class ModuleBuildError(Exception):
    pass


def _version_code(version: str) -> str:
    digits = "".join(ch for ch in version if ch.isdigit())
    if digits:
        return digits[-9:]
    return str(int(time.time()))


def _extract_stock_splits(bundle_path: str, architecture: str, dest_dir: str) -> list[str]:
    """Extract just enough from a downloaded .apkm bundle to force-install a
    working copy of the stock app: base.apk, plus the native-lib split(s)
    for `architecture` (every architecture's lib split when architecture ==
    "universal").

    Density/language config splits are deliberately dropped. A real
    Play-Store install never receives every locale/density split either --
    only the ones matching that specific device -- so base.apk alone
    (which already embeds default/fallback resources) plus the correct
    native libraries installs and runs fine; per Android's own app bundle
    docs, density/language config splits are always optional, never
    required. This matters a lot in practice: those splits made up the
    overwhelming majority of a module's size (a real build showed 266MB for
    YouTube, almost all of it ~30 language/density splits), and none of
    their *resources* end up mattering anyway once the patched base.apk is
    mounted over this stock install -- only its native libraries stay in
    use, so shipping the rest was pure waste of build time and download size.
    """
    os.makedirs(dest_dir, exist_ok=True)
    written: list[str] = []

    with zipfile.ZipFile(bundle_path) as z:
        for name in z.namelist():
            if not name.lower().endswith(".apk"):
                continue
            base_name = os.path.basename(name)
            # Match a dot-delimited segment exactly (e.g. the "x86_64" in
            # "split_config.x86_64.apk") rather than a substring check --
            # "x86" is a substring of "x86_64", so a naive `in` check would
            # incorrectly keep the x86_64 lib split when building for x86.
            segments = base_name.lower().replace("-", "_").split(".")

            matched_arch = next(
                (arch for arch, kw in ARCH_SPLIT_KEYWORDS.items() if kw in segments),
                None,
            )
            is_base = base_name.lower() == "base.apk"

            if not is_base and matched_arch is None:
                continue  # a density or language config split -- not needed
            if matched_arch is not None and architecture != "universal" and matched_arch != architecture:
                continue  # a different architecture's native-lib split

            dest_path = os.path.join(dest_dir, base_name)
            with z.open(name) as src, open(dest_path, "wb") as dst:
                dst.write(src.read())
            written.append(dest_path)

    if not written:
        raise ModuleBuildError(f"No .apk entries found inside bundle: {bundle_path}")
    return written


# ---------------------------------------------------------------------------
# Script templates. Kept short and readable rather than densely "clever" --
# these run as /system/bin/sh (Android's ash-based shell), so no bashisms.
# ---------------------------------------------------------------------------

MODULE_PROP_TEMPLATE = """\
id={module_id}
name={name}
version=v{version}
versionCode={version_code}
author={author}
description={description}
"""

MODULE_CONF_TEMPLATE = """\
MODULE_ID="{module_id}"
MODULE_PKG_NAME="{package_name}"
MODULE_PKG_VERSION="{version}"
MODULE_APP_NAME="{app_label}"
MODULE_ARCH_LIB="{arch_lib}"
"""

MOUNT_LIB_SH = """\
#!/system/bin/sh
# Shared helpers for this module's customize.sh / service.sh / action.sh /
# uninstall.sh. The caller must set $MODDIR before sourcing this file.

. "$MODDIR/module.conf"

# Parked outside $MODDIR so tools that hide the module folder from a target
# app (to dodge root detection) don't also hide the file we mount from.
PARKED_APK="/data/adb/morphe_mounts/${MODULE_ID}.apk"

pkg_installed_version() {
	dumpsys package "$MODULE_PKG_NAME" 2>/dev/null \\
		| grep -m1 'versionName=' | sed 's/^.*versionName=//' | cut -d' ' -f1
}

pkg_base_apk_path() {
	pm path "$MODULE_PKG_NAME" 2>/dev/null | grep -m1 '/base\\.apk$' | sed 's/^package://'
}

install_stock_apks() {
	TOTAL_BYTES=0
	for f in "$MODDIR"/stock/*.apk; do
		[ -f "$f" ] || continue
		SZ=$(stat -c '%s' "$f" 2>/dev/null) || SZ=0
		TOTAL_BYTES=$((TOTAL_BYTES + SZ))
	done
	if [ "$TOTAL_BYTES" = 0 ]; then
		echo "! No stock APKs bundled in this module" >&2
		return 1
	fi

	V_ADB=$(settings get global verifier_verify_adb_installs 2>/dev/null)
	V_PKG=$(settings get global package_verifier_enable 2>/dev/null)
	settings put global verifier_verify_adb_installs 0
	settings put global package_verifier_enable 0

	OK=1
	SESSION_OUT=$(pm install-create --user 0 -r -d -g -i com.android.vending -S "$TOTAL_BYTES" 2>&1)
	SESSION_ID=$(echo "$SESSION_OUT" | sed -n 's/.*\\[\\([0-9][0-9]*\\)\\].*/\\1/p')
	if [ -z "$SESSION_ID" ]; then
		echo "! install-create failed: $SESSION_OUT" >&2
		OK=0
	fi

	if [ "$OK" = 1 ]; then
		i=0
		for f in "$MODDIR"/stock/*.apk; do
			[ -f "$f" ] || continue
			i=$((i + 1))
			FSZ=$(stat -c '%s' "$f" 2>/dev/null) || FSZ=0
			if ! WOUT=$(pm install-write -S "$FSZ" "$SESSION_ID" "split_${i}.apk" "$f" 2>&1); then
				echo "! install-write failed for $f: $WOUT" >&2
				OK=0
				break
			fi
		done
	fi

	if [ "$OK" = 1 ]; then
		COUT=$(pm install-commit "$SESSION_ID" 2>&1)
		if ! echo "$COUT" | grep -qi success; then
			echo "! install-commit failed: $COUT" >&2
			OK=0
		fi
	else
		[ -n "$SESSION_ID" ] && pm install-abandon "$SESSION_ID" >/dev/null 2>&1
	fi

	[ -n "$V_ADB" ] && settings put global verifier_verify_adb_installs "$V_ADB"
	[ -n "$V_PKG" ] && settings put global package_verifier_enable "$V_PKG"

	[ "$OK" = 1 ]
}

unmount_patched_apk() {
	su -M -c "grep -F \\"$MODULE_PKG_NAME\\" /proc/mounts" 2>/dev/null | while read -r LINE; do
		MP=${LINE#* } MP=${MP%% *}
		su -M -c "umount -l \\"$MP\\"" 2>/dev/null
	done
}

mount_patched_apk() {
	BASEPATH=$(pkg_base_apk_path)
	if [ -z "$BASEPATH" ]; then
		echo "! Could not resolve installed base.apk path for $MODULE_PKG_NAME" >&2
		return 1
	fi

	mkdir -p "$(dirname "$PARKED_APK")"
	if [ -f "$MODDIR/base.apk" ]; then
		mv -f "$MODDIR/base.apk" "$PARKED_APK"
	fi
	if [ ! -f "$PARKED_APK" ]; then
		echo "! Patched APK not found at $PARKED_APK" >&2
		return 1
	fi

	chcon u:object_r:apk_data_file:s0 "$PARKED_APK" 2>/dev/null
	unmount_patched_apk
	if ! OUT=$(su -M -c "mount -o bind \\"$PARKED_APK\\" \\"$BASEPATH\\"" 2>&1); then
		echo "! bind mount failed: $OUT" >&2
		return 1
	fi
	am force-stop "$MODULE_PKG_NAME" 2>/dev/null
	return 0
}
"""

CUSTOMIZE_SH = """\
#!/system/bin/sh
MODDIR="$MODPATH"
. "$MODDIR/mount_lib.sh"

ui_print " "
ui_print "- $MODULE_APP_NAME (Morphe)"

# Magisk/KernelSU only provide $ARCH (arm / arm64 / x86 / x64) -- map it to
# our own arch naming so it can be compared against $MODULE_ARCH_LIB below.
# BUGFIX: this mapping was missing entirely, so the check below was always
# comparing against an empty string and aborting on every device.
case "$ARCH" in
	arm) DEVICE_ARCH_LIB=armeabi-v7a ;;
	arm64) DEVICE_ARCH_LIB=arm64-v8a ;;
	x86) DEVICE_ARCH_LIB=x86 ;;
	x64) DEVICE_ARCH_LIB=x86_64 ;;
	*) DEVICE_ARCH_LIB="" ;;
esac

if [ -n "$MODULE_ARCH_LIB" ] && [ "$DEVICE_ARCH_LIB" != "$MODULE_ARCH_LIB" ]; then
	abort "! This module was built for $MODULE_ARCH_LIB, this device reports $DEVICE_ARCH_LIB (ARCH=$ARCH)"
fi

set_perm_recursive "$MODPATH" 0 0 0755 0644 2>/dev/null

INSTALLED_VERSION=$(pkg_installed_version)
if [ "$INSTALLED_VERSION" = "$MODULE_PKG_VERSION" ]; then
	ui_print "- $MODULE_PKG_NAME $INSTALLED_VERSION is already installed"
elif [ -d "$MODDIR/stock" ] && [ -n "$(ls -A "$MODDIR/stock" 2>/dev/null)" ]; then
	if [ -n "$INSTALLED_VERSION" ]; then
		ui_print "- Replacing installed $MODULE_PKG_NAME $INSTALLED_VERSION with $MODULE_PKG_VERSION"
	else
		ui_print "- $MODULE_PKG_NAME isn't installed, installing $MODULE_PKG_VERSION"
	fi
	if ! install_stock_apks; then
		abort "! Could not install stock $MODULE_PKG_NAME. See the log above for the reason."
	fi
else
	abort "! $MODULE_PKG_NAME $MODULE_PKG_VERSION isn't installed and this module has no bundled stock APKs."
fi

ui_print "- Mounting patched $MODULE_APP_NAME"
if ! mount_patched_apk; then
	ui_print "! Mount failed now -- it will retry automatically on next boot."
fi

am force-stop "$MODULE_PKG_NAME" 2>/dev/null
rm -rf "$MODDIR/stock"
ui_print "- Done"
"""

SERVICE_SH = """\
#!/system/bin/sh
MODDIR=$(dirname "$(readlink -f "$0")")
. "$MODDIR/mount_lib.sh"

# Bind mounts don't survive a reboot -- re-apply once boot has finished and
# the package manager is responsive again.
until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 1; done
sleep 5

TRIES=0
while [ "$TRIES" -lt 30 ]; do
	[ -n "$(pkg_base_apk_path)" ] && break
	TRIES=$((TRIES + 1))
	sleep 2
done

mount_patched_apk
"""

UNINSTALL_SH = """\
#!/system/bin/sh
MODDIR=${0%/*}
. "$MODDIR/module.conf"
rm -f "/data/adb/morphe_mounts/${MODULE_ID}.apk"
rmdir "/data/adb/morphe_mounts" 2>/dev/null
"""

ACTION_SH = """\
#!/system/bin/sh
MODDIR=$(dirname "$(readlink -f "$0")")
. "$MODDIR/mount_lib.sh"

if [ -n "$(su -M -c "grep -F \\"$MODULE_PKG_NAME\\" /proc/mounts" 2>/dev/null)" ]; then
	unmount_patched_apk
	am force-stop "$MODULE_PKG_NAME" 2>/dev/null
	echo "* Unmounted -- $MODULE_APP_NAME reverted to stock until next toggle/reboot"
else
	if mount_patched_apk; then
		echo "* Mounted -- $MODULE_APP_NAME is patched"
	else
		echo "* Failed to mount, see the module's log"
	fi
fi
"""

UPDATE_BINARY = """\
#!/sbin/sh
umask 022
ui_print() { echo "$1"; }
require_new_magisk() {
  ui_print "*******************************"
  ui_print " Please install Magisk v20.4+! "
  ui_print "*******************************"
  exit 1
}
OUTFD=$2
ZIPFILE=$3
mount /data 2>/dev/null
[ -f /data/adb/magisk/util_functions.sh ] || require_new_magisk
. /data/adb/magisk/util_functions.sh
[ "$MAGISK_VER_CODE" -lt 20400 ] && require_new_magisk
install_module
exit 0
"""

UPDATER_SCRIPT = "#MAGISK\n"


def build_magisk_module(
    app: AppConfig,
    patched_apk_path: str,
    stock_bundle_path: str,
    version: str,
    architecture: str,
    out_path: str,
) -> str:
    assert app.module is not None
    module_id = app.module.id
    author = app.module.author
    arch_lib = "" if architecture == "universal" else architecture
    app_label = f"{app.name} (Morphe, {architecture})"

    description = (
        f"Force-installs stock {app.name} {version} if needed, then mounts "
        f"the Morphe-patched APK over it. Requires root. Experimental."
    )

    module_prop = MODULE_PROP_TEMPLATE.format(
        module_id=module_id,
        name=app_label,
        version=version,
        version_code=_version_code(version),
        author=author,
        description=description,
    )
    module_conf = MODULE_CONF_TEMPLATE.format(
        module_id=module_id,
        package_name=app.package_name,
        version=version,
        app_label=app_label,
        arch_lib=arch_lib,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    stock_dir = os.path.join(os.path.dirname(out_path), f".stock-{module_id}-{architecture}")
    try:
        stock_files = _extract_stock_splits(stock_bundle_path, architecture, stock_dir)

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("module.prop", module_prop)
            z.writestr("module.conf", module_conf)
            z.writestr("mount_lib.sh", MOUNT_LIB_SH)
            z.writestr("customize.sh", CUSTOMIZE_SH)
            z.writestr("service.sh", SERVICE_SH)
            z.writestr("uninstall.sh", UNINSTALL_SH)
            z.writestr("action.sh", ACTION_SH)
            z.writestr("META-INF/com/google/android/update-binary", UPDATE_BINARY)
            z.writestr("META-INF/com/google/android/updater-script", UPDATER_SCRIPT)
            z.write(patched_apk_path, "base.apk")
            for f in stock_files:
                z.write(f, f"stock/{os.path.basename(f)}")
    finally:
        for f in os.listdir(stock_dir) if os.path.isdir(stock_dir) else []:
            os.remove(os.path.join(stock_dir, f))
        if os.path.isdir(stock_dir):
            os.rmdir(stock_dir)

    return out_path
