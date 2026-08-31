"""Generic downloader for GitHub-released build tools: the Morphe CLI jar and
any app's patches bundle (*.mpp / *.rvp).

This replaces the old Instagram-only download_bins.py. It works for any repo
that publishes a matching asset on its GitHub Releases page, whether that's
MorpheApp's own patches, a third-party fork like crimera/piko, or anything
else -- nothing here is app-specific.
"""

import os
import re

from config import SourceConfig
from github import GithubRelease, get_release_by_tag, list_releases
from utils import download


class NoMatchingReleaseError(Exception):
    pass


class NoMatchingAssetError(Exception):
    pass


def _pick_release(source: SourceConfig, pinned_tag: str | None) -> GithubRelease:
    if pinned_tag:
        release = get_release_by_tag(source.repo, pinned_tag)
        if release is None:
            raise NoMatchingReleaseError(
                f"{source.repo}: no release found for pinned tag '{pinned_tag}'"
            )
        return release

    for release in list_releases(source.repo):
        if source.include_prereleases or not release.prerelease:
            return release

    raise NoMatchingReleaseError(
        f"{source.repo}: no releases found (channel={source.channel})"
    )


def download_github_asset(
    source: SourceConfig,
    out_dir: str,
    filename: str,
    pinned_tag: str | None = None,
    skip_if_exists: bool = True,
) -> str | None:
    """Download the first release asset matching `source.asset_regex`.

    Returns the tag (e.g. "v3.9.0") it was pulled from, so callers can show
    it in release notes, or None if it couldn't be determined (e.g. the file
    was already cached from a previous run and no sidecar tag was found).

    `skip_if_exists` avoids even checking GitHub when the target file is
    already on disk -- useful for local testing/reruns, and cheap insurance
    against burning API rate limit on files we already have. The resolved
    tag is remembered in a small sidecar file so it survives that skip.
    """
    out_path = f"{out_dir.rstrip('/')}/{filename}"
    tag_sidecar = f"{out_path}.source_tag"

    if skip_if_exists and os.path.exists(out_path):
        print(f"{out_path} already exists, skipping GitHub lookup entirely")
        if os.path.exists(tag_sidecar):
            return open(tag_sidecar).read().strip()
        return None

    release = _pick_release(source, pinned_tag)

    matching_asset = next(
        (a for a in release.assets if re.search(source.asset_regex, a.name)),
        None,
    )
    if matching_asset is None:
        available = ", ".join(a.name for a in release.assets) or "(no assets)"
        raise NoMatchingAssetError(
            f"{source.repo}@{release.tag_name}: no asset matches "
            f"'{source.asset_regex}'. Available assets: {available}"
        )

    download(matching_asset.browser_download_url, out_path)
    with open(tag_sidecar, "w") as f:
        f.write(release.tag_name)
    return release.tag_name


def download_cli(source: SourceConfig, out_dir: str = "bins") -> str | None:
    print(f"Downloading CLI from {source.repo} (channel={source.channel})")
    return download_github_asset(source, out_dir, "morphe-cli.jar")


def download_patches(
    source: SourceConfig, out_dir: str = "bins", pinned_tag: str | None = None
) -> str | None:
    print(f"Downloading patches from {source.repo} (channel={source.channel})")
    return download_github_asset(source, out_dir, "patches.mpp", pinned_tag=pinned_tag)
