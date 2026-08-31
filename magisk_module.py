"""Builds a flashable Magisk/KernelSU module (.zip) from a patched APK.

IMPORTANT CAVEATS (please read before relying on this):

Morphe/ReVanced's own CLI does not produce Magisk modules -- its `--mount`
flag only pushes a patched APK to an already-connected ADB device at patch
time, it doesn't produce a distributable module artifact. So this file
implements the well-established community pattern instead (the same one
used by tools like j-hc/revanced-magisk-module): the module's service script
bind-mounts our patched APK directly over the ALREADY-INSTALLED stock app's
own APK file at every boot, using root. This means:

  * The exact same version must already be installed from the Play Store
    (or sideloaded) on the device -- this module does not install anything
    on its own.
  * It requires root (Magisk or KernelSU) and needs the module enabled +
    a reboot to take effect.
  * It assumes the installed app is a single, non-split APK. Play Store
    installs are frequently SPLIT into base.apk + several split_*.apk files
    (for architecture/density/language); mounting a single merged APK over
    just base.apk in that situation is not guaranteed to work correctly.
    Morphe's own single-file `-o` output is a merged, non-split APK, so this
    is the same tradeoff as any other Magisk-mount-based patched-app module.

This code cannot be exercised against a real rooted device inside a CI
sandbox, so build_mode "module"/"both" should be treated as experimental --
please test on a real device before relying on it. build_mode "apk" (the
default) does not use any of this and is unaffected.
"""

import os
import time
import zipfile

from config import AppConfig

MODULE_PROP_TEMPLATE = """\
id={module_id}
name={name}
version=v{version}
versionCode={version_code}
author=Morphe Multi-App Builder
description={description}
"""

CUSTOMIZE_SH_TEMPLATE = """\
#!/sbin/sh
# shellcheck shell=sh
SKIPUNZIP=0

ui_print "- Installing {name} (Morphe patched, mounted over the stock app)"
ui_print "- Package: {package_name}"
ui_print "- Requires the SAME version already installed, plus a reboot."
"""

SERVICE_SH_TEMPLATE = """\
#!/system/bin/sh
# Runs at late_start service (i.e. once /data is decrypted and the package
# manager is up). Bind-mounts our patched APK over the stock app's own APK
# file so the system loads our patched code without a real re-install.
MODDIR=${{0%/*}}
PKG="{package_name}"
PATCHED_APK="$MODDIR/app.apk"

log_tag="{module_id}"

if [ ! -f "$PATCHED_APK" ]; then
    log -t "$log_tag" "patched apk missing at $PATCHED_APK, skipping mount"
    exit 0
fi

# Wait (briefly) for the package manager service to be ready.
i=0
while [ "$i" -lt 60 ]; do
    if pm path "$PKG" >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 1
done

{resolve_target}

if [ -z "$TARGET_APK" ]; then
    log -t "$log_tag" "could not resolve installed apk path for $PKG -- is it installed? not mounting."
    exit 0
fi

if [ ! -f "$TARGET_APK" ]; then
    log -t "$log_tag" "resolved path $TARGET_APK does not exist, not mounting"
    exit 0
fi

mount -o bind "$PATCHED_APK" "$TARGET_APK" \\
    && log -t "$log_tag" "mounted $PATCHED_APK over $TARGET_APK" \\
    || log -t "$log_tag" "failed to bind-mount over $TARGET_APK"
"""

UNINSTALL_SH_TEMPLATE = """\
#!/system/bin/sh
# The bind mount created by service.sh only lives for the current boot and
# is torn down automatically on the next reboot once this module has been
# removed -- nothing else to clean up here.
"""

AUTO_RESOLVE_TARGET = """\
TARGET_APK=$(pm path "$PKG" 2>/dev/null | grep "base.apk" | head -n1 | sed 's/^package://')
if [ -z "$TARGET_APK" ]; then
    # Some ROMs/apps only ever report a single, non-split path.
    TARGET_APK=$(pm path "$PKG" 2>/dev/null | head -n1 | sed 's/^package://')
fi
"""


def _version_code(version: str) -> str:
    digits = "".join(ch for ch in version if ch.isdigit())
    if digits:
        # Keep it a reasonable size for a 32-bit versionCode.
        return digits[-9:]
    return str(int(time.time()))


def build_magisk_module(
    app: AppConfig,
    patched_apk_path: str,
    version: str,
    architecture: str,
    out_path: str,
) -> str:
    assert app.module is not None
    module_id = app.module.id

    if app.module.mount_path == "auto":
        resolve_target = AUTO_RESOLVE_TARGET
    else:
        escaped_path = app.module.mount_path.replace('"', '\\"')
        resolve_target = f'TARGET_APK="{escaped_path}"'

    description = (
        f"Mounts the Morphe-patched {app.name} ({architecture}) over the "
        f"installed app. Requires root + the same version already "
        f"installed + a reboot. Experimental."
    )

    module_prop = MODULE_PROP_TEMPLATE.format(
        module_id=module_id,
        name=f"{app.name} (Morphe, {architecture})",
        version=version,
        version_code=_version_code(version),
        description=description,
    )
    customize_sh = CUSTOMIZE_SH_TEMPLATE.format(
        name=app.name, package_name=app.package_name
    )
    service_sh = SERVICE_SH_TEMPLATE.format(
        package_name=app.package_name,
        module_id=module_id,
        resolve_target=resolve_target,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("module.prop", module_prop)
        z.writestr("customize.sh", customize_sh)
        z.writestr("service.sh", service_sh)
        z.writestr("uninstall.sh", UNINSTALL_SH_TEMPLATE)
        # Magisk needs an empty skip_mount / no auto-mount marker only if we
        # don't want its default /system overlay behaviour -- we don't ship
        # a /system tree at all (only service.sh does work), so nothing to
        # add here.
        z.write(patched_apk_path, "app.apk")

    return out_path
