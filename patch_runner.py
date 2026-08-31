"""Runs the Morphe CLI's `patch` command generically for any app.

Replaces the old Instagram-only build_variants.py. Notable design points:

  1. Architecture handling uses the CLI's own `--striplibs` flag instead of
     downloading a separate APKMirror variant per architecture. One bundle
     download is patched multiple times (once per configured architecture),
     each time stripping down to just that architecture's native libraries
     -- confirmed via `patch --help` on the real CLI. Fewer network calls,
     fewer chances for APKMirror scraping to fail partway through a run.

  2. build_mode support (apk / module / both). Critically, the "apk" output
     and the "module" output are now patched SEPARATELY with different
     patch selections, not the same file reused for both. This matters
     because of a confirmed real bug: "GmsCore support" (which lets a
     non-rooted APK work without Google Play Services by faking a
     different package name) is enabled by default in the real YouTube/
     YouTube Music patches, but actively breaks a root/mount install --
     the patched app itself shows "Do not include 'GmsCore support' patch
     with root install" at runtime when both are combined. So the module
     build always forces it off, while the plain APK build keeps using it
     (or whatever the user configured) since non-rooted installs need it.
"""

import os
import subprocess
from dataclasses import dataclass

from config import AppConfig
from magisk_module import build_magisk_module

KEYSTORE_ARGS = [
    # Re-use j-hc's well-known keystore so patched apps keep updating in
    # place across builds/forks instead of forcing a fresh install.
    "--keystore", "ks.keystore",
    "--keystore-entry-password", "123456789",
    "--keystore-password", "123456789",
    "--signer", "jhc",
    "--keystore-entry-alias", "jhc",
]

# Confirmed via `list-patches --with-packages --with-options` against the
# real MorpheApp/morphe-patches bundle: "GmsCore support" is Enabled: true
# by default for YouTube and YouTube Music, and is specifically for
# non-rooted installs. It must never be applied to a "module" (root-mount)
# build, regardless of what the user's own include/exclude lists say.
MODULE_FORCED_EXCLUDES = ["GmsCore support"]


@dataclass
class BuildOutput:
    kind: str  # "apk" or "module"
    architecture: str
    path: str


class PatchFailedError(Exception):
    pass


def _run_patch_command(
    cli_jar: str,
    patches_files: list[str],
    apk_path: str,
    out_path: str,
    include_patches: list[str] | None,
    exclude_patches: list[str] | None,
    striplibs: str | None,
    force: bool,
) -> None:
    command = ["java", "-jar", cli_jar, "patch"]

    for pf in patches_files:
        command.extend(["-p", pf])

    for name in include_patches or []:
        command.extend(["-e", name])
    for name in exclude_patches or []:
        command.extend(["-d", name])

    command.extend(KEYSTORE_ARGS)

    if striplibs:
        command.append(f"--striplibs={striplibs}")
    if force:
        command.append("--force")

    command.extend(["-o", out_path])
    command.append(apk_path)

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise PatchFailedError(
            f"Morphe CLI patch failed for {apk_path} "
            f"(striplibs={striplibs or 'universal'}):\n{result.stdout}\n{result.stderr}"
        )

    if not os.path.exists(out_path):
        raise PatchFailedError(
            f"Morphe CLI reported success but did not create {out_path}"
        )


def _module_excludes(app: AppConfig) -> list[str]:
    # dict.fromkeys(...) dedupes while preserving order (no set needed).
    return list(dict.fromkeys([*app.exclude_patches, *MODULE_FORCED_EXCLUDES]))


def build_all_outputs(
    app: AppConfig,
    cli_jar: str,
    patches_files: list[str],
    downloaded_apk_path: str,
    version: str,
    output_dir: str,
    force: bool = False,
) -> list[BuildOutput]:
    """Build every configured (architecture x apk/module) output for `app`.

    `downloaded_apk_path` (the single APKMirror bundle download) is reused
    as the patch INPUT for every architecture, and also as the source of
    the stock split APKs bundled into any "module" output -- see
    magisk_module.py.
    """
    os.makedirs(output_dir, exist_ok=True)
    outputs: list[BuildOutput] = []

    for arch in app.architectures:
        striplibs = None if arch == "universal" else arch

        if app.wants_apk:
            apk_out = os.path.join(output_dir, f"{app.id}-v{version}-{arch}.apk")
            print(f"[{app.id}] Patching {arch} (apk) -> {apk_out}")
            _run_patch_command(
                cli_jar=cli_jar,
                patches_files=patches_files,
                apk_path=downloaded_apk_path,
                out_path=apk_out,
                include_patches=app.include_patches,
                exclude_patches=app.exclude_patches,
                striplibs=striplibs,
                force=force,
            )
            outputs.append(BuildOutput(kind="apk", architecture=arch, path=apk_out))

        if app.wants_module:
            assert app.module is not None  # enforced by config.py validation

            # Separate patch run, on purpose -- see MODULE_FORCED_EXCLUDES.
            module_payload = os.path.join(
                output_dir, f".{app.id}-v{version}-{arch}-module-payload.apk"
            )
            print(f"[{app.id}] Patching {arch} (module) -> {module_payload}")
            _run_patch_command(
                cli_jar=cli_jar,
                patches_files=patches_files,
                apk_path=downloaded_apk_path,
                out_path=module_payload,
                include_patches=app.include_patches,
                exclude_patches=_module_excludes(app),
                striplibs=striplibs,
                force=force,
            )

            module_out = os.path.join(
                output_dir, f"{app.id}-v{version}-{arch}-module.zip"
            )
            print(f"[{app.id}] Building Magisk/KernelSU module -> {module_out}")
            try:
                build_magisk_module(
                    app=app,
                    patched_apk_path=module_payload,
                    stock_bundle_path=downloaded_apk_path,
                    version=version,
                    architecture=arch,
                    out_path=module_out,
                )
            finally:
                if os.path.exists(module_payload):
                    os.remove(module_payload)

            outputs.append(
                BuildOutput(kind="module", architecture=arch, path=module_out)
            )

    return outputs
