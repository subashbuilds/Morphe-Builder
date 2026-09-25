"""Generic multi-app Morphe builder.

Reads config.yml, and for every enabled app:
  1. Downloads the CLI + patches bundle it needs (cached across apps that
     share the same repo+channel, e.g. YouTube & YouTube Music both use
     MorpheApp/morphe-patches).
  2. Asks the CLI itself which app version(s) its patches currently support
     best (version_resolver.py), falling back to APKMirror's own version
     listing only if the patches place no restriction on version.
  3. Tries those candidate versions newest-first: selects the configured
     architecture/DPI source from APKMirror, and if that upload/build fails
     for any reason, moves on to the next candidate instead of failing the run.
  4. Builds every configured architecture x apk/module output from its selected
     source (patch_runner.py), and publishes a GitHub release tagged
     '<app-id>-<version>'.

Usage:
    python main.py                          # build every enabled app
    python main.py --app youtube             # build just one app
    python main.py --app youtube --version 21.04.223   # pin a version
    python main.py --force                   # rebuild even if already released
    python main.py --check                   # resolve versions only, no
                                              # download/patch/publish -- good
                                              # for sanity-checking config.yml
                                              # and connectivity in CI or
                                              # locally without FlareSolverr
"""

import argparse
import hashlib
import os
import re
import traceback

import apkmirror
import github
from cli_tools import download_cli, download_patches
from config import AppConfig, ConfigError, enabled_apps, load_config
from constants import BINS_DIR, OUTPUT_DIR, get_repo
from patch_runner import BuildOutput, PatchFailedError, build_all_outputs
from utils import FlareSolverrSession, panic, publish_release, report_to_telegram
from version_resolver import ListVersionsError, get_supported_versions

# Cache downloaded CLI/patches files across apps that share the same
# (repo, channel) so e.g. YouTube and YouTube Music don't each redownload
# the same MorpheApp/morphe-patches release.
_download_cache: dict[tuple[str, str, str], tuple[str, str | None]] = {}


def _cached_download(kind: str, source, out_subdir: str) -> tuple[str, str | None]:
    key = (kind, source.repo, source.channel)
    if key in _download_cache:
        return _download_cache[key]

    out_dir = os.path.join(BINS_DIR, out_subdir)
    if kind == "cli":
        tag = download_cli(source, out_dir=out_dir)
        path = os.path.join(out_dir, "morphe-cli.jar")
    else:
        tag = download_patches(source, out_dir=out_dir)
        path = os.path.join(out_dir, "patches.mpp")

    _download_cache[key] = (path, tag)
    return path, tag


def _release_tag(app: AppConfig, version: str) -> str:
    return f"{app.id}-{version}"


def _already_released_versions(repo: str, app: AppConfig) -> dict[str, "github.GithubRelease"]:
    """Map version -> the existing release for it, for every version of
    `app` this repo has already published."""
    prefix = f"{app.id}-"
    return {
        release.tag_name[len(prefix):]: release
        for release in github.list_releases(repo)
        if release.tag_name.startswith(prefix)
    }


_RECORDED_PATCHES_TAG = re.compile(r"^Patches:\s+\S+\s+(\S+)\s+\(channel:", re.MULTILINE)
_RECORDED_VARIANTS = re.compile(r"^APKMirror variants:\s+(.+)$", re.MULTILINE)


def _variant_signature(app: AppConfig) -> str:
    dpi = f"{app.dpi}dpi" if app.dpi and app.dpi.isdigit() else (app.dpi or "auto")
    return "; ".join(f"{arch}@{dpi}" for arch in app.architectures)


def _recorded_patches_tag(release: "github.GithubRelease") -> str | None:
    """Pull the patches tag we ourselves wrote into a past release's notes
    (see the `message = ...` block below), so we can tell whether the
    patches have moved on since that release was built. Returns None if it
    can't be determined (including the "(version unknown)" case, which
    contains a space and so never matches \\S+ -- treated the same as "no
    information available").
    """
    match = _RECORDED_PATCHES_TAG.search(release.body or "")
    return match.group(1) if match else None


def _resolve_candidate_versions(
    app: AppConfig, cli_jar: str, patches_path: str, session: FlareSolverrSession
) -> list[str]:
    if app.pinned_version:
        return [app.pinned_version]

    try:
        candidates = get_supported_versions(cli_jar, [patches_path], app.package_name)
    except ListVersionsError as e:
        print(f"[{app.id}] list-versions failed, will fall back to APKMirror: {e}")
        candidates = []

    if candidates:
        return candidates

    print(
        f"[{app.id}] patches place no version restriction on {app.package_name}; "
        "falling back to APKMirror's own version listing"
    )
    versions = apkmirror.get_versions(app.apkmirror_listing_url(), session=session)
    return [v.version for v in versions]


def build_app(
    app: AppConfig,
    repo: str,
    session: FlareSolverrSession,
    force: bool,
    version_override: str | None,
    check_only: bool,
) -> tuple[str, list[BuildOutput], str | None, str | None] | None:
    """Build one app. Returns (version, outputs, patches_tag, cli_tag) on success, None if skipped."""
    print(f"\n=== {app.name} ({app.id}) ===")

    cli_jar, cli_tag = _cached_download("cli", app.cli, out_subdir=f"cli-{app.cli.repo.replace('/', '_')}")
    patches_path, patches_tag = _cached_download(
        "patches", app.patches, out_subdir=f"patches-{app.patches.repo.replace('/', '_')}"
    )

    if version_override:
        candidates = [version_override]
    else:
        candidates = _resolve_candidate_versions(app, cli_jar, patches_path, session)

    if not candidates:
        print(f"[{app.id}] Could not determine any candidate version to build. Skipping.")
        return None

    print(f"[{app.id}] Candidate versions (best first): {candidates}")

    if check_only:
        print(f"[{app.id}] --check: candidates resolved OK, stopping before any network/publish step.")
        return None

    already_released = {} if force else _already_released_versions(repo, app)
    variant_signature = _variant_signature(app)

    output_dir = os.path.join(OUTPUT_DIR, app.id)

    for version in candidates:
        existing_release = already_released.get(version)
        if existing_release is not None:
            recorded_tag = _recorded_patches_tag(existing_release)
            variant_match = re.search(
                _RECORDED_VARIANTS, existing_release.body or ""
            )
            recorded_variants = variant_match.group(1).strip() if variant_match else None
            variants_changed = recorded_variants != variant_signature
            if (recorded_tag is None or recorded_tag == patches_tag) and not variants_changed:
                # BUGFIX: this used to pre-filter every already-released
                # version out of the list and then happily attempt
                # whatever was left -- which meant if the *best* candidate
                # (e.g. 21.04.223) was already released, it would fall
                # through and publish an OLDER candidate (e.g. 20.51.39) as
                # if it were new. Candidates are best-first, so hitting an
                # already-released one (with unchanged patches) means we
                # already have the best version currently buildable --
                # stop here rather than downgrading.
                print(f"[{app.id}] {version} is already released (patches unchanged) "
                      f"and is the best currently available candidate. Nothing new to build.")
                return None
            # BUGFIX: a patches repo can publish a new release that adds/
            # fixes patches without the target app version changing at all
            # (common when the app itself hasn't updated recently). Only
            # comparing app versions meant this case was silently treated
            # as "nothing to do" forever. If the *recorded* patches tag on
            # the existing release differs from the one we just downloaded,
            # this version needs rebuilding even though it was released
            # before -- publish_release() already knows how to overwrite an
            # existing tag, so this falls through to a normal build below.
            reasons = []
            if recorded_tag != patches_tag:
                reasons.append(f"patches changed ({recorded_tag} -> {patches_tag})")
            if variants_changed:
                reasons.append(
                    f"variant config changed ({recorded_variants or 'unknown'} -> {variant_signature})"
                )
            print(f"[{app.id}] Rebuilding {version}: {'; '.join(reasons)}.")

        print(f"[{app.id}] Attempting version {version}...")
        try:
            release_url = app.apkmirror_release_url(version)
            variants = apkmirror.get_variants(release_url, session=session)
            downloaded_paths: dict[str, str] = {}
            path_by_link: dict[str, str] = {}

            for arch in app.architectures:
                variant = apkmirror.select_variant(
                    variants,
                    arch,
                    dpi=app.dpi,
                    prefer_bundle=app.wants_module,
                    bundle_only=app.wants_module,
                )
                source_path = path_by_link.get(variant.link)
                if source_path is None:
                    digest = hashlib.sha256(variant.link.encode()).hexdigest()[:12]
                    extension = "apkm" if variant.is_bundle else "apk"
                    source_path = os.path.join(
                        BINS_DIR,
                        "downloads",
                        f"{app.id}-{version}-{digest}.{extension}",
                    )
                    apkmirror.download_apk(variant, source_path, session=session)
                    path_by_link[variant.link] = source_path
                    size_mb = os.path.getsize(source_path) / (1024 * 1024)
                    print(f"[{app.id}] Selected {variant.architecture}/{variant.dpi or 'unknown DPI'} ({size_mb:.1f} MiB).")
                else:
                    print(f"[{app.id}] Reusing selected {variant.architecture} source for {arch}.")
                downloaded_paths[arch] = source_path

            outputs = build_all_outputs(
                app=app,
                cli_jar=cli_jar,
                patches_files=[patches_path],
                downloaded_apk_paths=downloaded_paths,
                version=version,
                output_dir=output_dir,
            )
            print(f"[{app.id}] Successfully built {len(outputs)} output(s) for {version}.")
            return version, outputs, patches_tag, cli_tag

        except (apkmirror.FailedToFetch, apkmirror.FailedToFindElement) as e:
            print(f"[{app.id}] APKMirror step failed for {version}: {e}. Trying next candidate.")
        except PatchFailedError as e:
            print(f"[{app.id}] Patching failed for {version}: {e}. Trying next candidate.")
        except Exception:
            print(f"[{app.id}] Unexpected error building {version}. Trying next candidate.")
            traceback.print_exc()

    print(f"[{app.id}] Exhausted all candidate versions without a successful build.")
    return None


def run(
    app_id: str | None,
    version_override: str | None,
    force: bool,
    check_only: bool,
) -> bool:
    """Returns True if at least one app was successfully built and published."""
    try:
        all_apps = load_config("config.yml")
    except ConfigError as e:
        panic(f"config.yml error: {e}")
        return False

    if app_id:
        apps = [a for a in all_apps if a.id == app_id]
        if not apps:
            panic(f"No app with id '{app_id}' found in config.yml")
            return False
        if not apps[0].enabled and not force:
            panic(f"App '{app_id}' is disabled in config.yml (enabled: false)")
            return False
    else:
        apps = enabled_apps("config.yml")
        if not apps:
            print("No enabled apps in config.yml. Nothing to do.")
            return False

    repo = get_repo() if not check_only else os.environ.get("GITHUB_REPOSITORY", "local/check")

    os.makedirs(BINS_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    built_any = False
    built_tags: list[str] = []

    with FlareSolverrSession() as session:
        for app in apps:
            result = build_app(
                app,
                repo=repo,
                session=session,
                force=force,
                version_override=version_override if app_id else None,
                check_only=check_only,
            )
            if result is None:
                continue

            version, outputs, patches_tag, cli_tag = result
            tag = _release_tag(app, version)
            variant_signature = _variant_signature(app)
            arch_order = {arch: i for i, arch in enumerate(app.architectures)}
            kind_order = {"apk": 0, "module": 1}
            ordered_outputs = sorted(
                outputs,
                key=lambda output: (
                    arch_order[output.architecture],
                    kind_order[output.kind],
                    output.path,
                ),
            )
            files = [output.path for output in ordered_outputs]

            message = (
                f"Automated {app.name} build.\n\n"
                f"Patches: {app.patches.repo} {patches_tag or '(version unknown)'} "
                f"(channel: {app.patches.channel})\n"
                f"CLI: {app.cli.repo} {cli_tag or '(version unknown)'} "
                f"(channel: {app.cli.channel})\n"
                f"Architectures: {', '.join(app.architectures)}\n"
                f"APKMirror variants: {variant_signature}\n"
                f"Build mode: {app.build_mode}\n"
            )

            publish_release(
                tag=tag,
                files=files,
                message=message,
                title=f"{app.name} v{version}",
            )
            built_any = True
            built_tags.append(tag)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_output={'true' if built_any else 'false'}\n")
            f.write(f"built_tags={','.join(built_tags)}\n")

    if built_any and os.environ.get("TG_TOKEN"):
        for tag in built_tags:
            try:
                report_to_telegram(tag=tag)
            except Exception as e:
                # Telegram notification failing should never fail an
                # otherwise-successful build+release.
                print(f"Telegram notification for {tag} failed (build still succeeded): {e}")
    elif built_any:
        print("TG_TOKEN not set, skipping Telegram notification.")

    return built_any


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic Morphe multi-app builder")
    parser.add_argument("--app", help="Only build this app id from config.yml")
    parser.add_argument("--version", help="Pin an exact version instead of auto-detecting (requires --app)")
    parser.add_argument("--force", action="store_true", help="Rebuild even if already released")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Resolve versions only; skip downloading/patching/publishing",
    )
    args = parser.parse_args()

    if args.version and not args.app:
        panic("--version requires --app")

    run(
        app_id=args.app,
        version_override=args.version,
        force=args.force,
        check_only=args.check,
    )
