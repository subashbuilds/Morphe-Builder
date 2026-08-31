"""Runs the Morphe CLI's `patch` command generically for any app.

Replaces the old Instagram-only build_variants.py. Two things changed on
purpose:

  1. Architecture handling now uses the CLI's own `--striplibs` flag instead
     of downloading a separate APKMirror variant per architecture. One
     bundle download is patched multiple times (once per configured
     architecture), each time stripping down to just that architecture's
     native libraries -- confirmed via `patch --help` on the real CLI. This
     is both fewer network calls and fewer chances for APKMirror scraping
     to fail partway through a run.
  2. build_mode support (apk / module / both) -- "module" hands the freshly
     patched APK to magisk_module.py to wrap into a flashable Magisk/KernelSU
     zip. See that file's docstring for important caveats.
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

    One `downloaded_apk_path` (the single APKMirror bundle download) is
    reused for every architecture -- see module docstring.
    """
    os.makedirs(output_dir, exist_ok=True)
    outputs: list[BuildOutput] = []

    for arch in app.architectures:
        striplibs = None if arch == "universal" else arch
        apk_out = os.path.join(output_dir, f"{app.id}-v{version}-{arch}.apk")

        print(f"[{app.id}] Patching {arch} -> {apk_out}")
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

        if app.wants_apk:
            outputs.append(BuildOutput(kind="apk", architecture=arch, path=apk_out))

        if app.wants_module:
            assert app.module is not None  # enforced by config.py validation
            module_out = os.path.join(
                output_dir, f"{app.id}-v{version}-{arch}-module.zip"
            )
            print(f"[{app.id}] Building Magisk/KernelSU module -> {module_out}")
            build_magisk_module(
                app=app,
                patched_apk_path=apk_out,
                version=version,
                architecture=arch,
                out_path=module_out,
            )
            outputs.append(
                BuildOutput(kind="module", architecture=arch, path=module_out)
            )

        # An "apk"-only build_mode doesn't need the module, and vice versa,
        # but if build_mode is "module" only, the intermediate APK above is
        # still needed as the module's payload -- delete it afterwards so
        # we don't publish an APK nobody asked for.
        if not app.wants_apk and os.path.exists(apk_out):
            os.remove(apk_out)

    return outputs
