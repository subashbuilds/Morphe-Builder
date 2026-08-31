"""Small GitHub REST API client.

BUGFIX (was the most likely cause of random CI failures): every call here now
sends the GITHUB_TOKEN (always available for free in GitHub Actions) as a
Bearer token. Unauthenticated requests to api.github.com are capped at 60/hour
*per runner IP range*, which is shared by every GitHub-hosted runner -- it is
extremely easy to burn through that just from one workflow run when several
templates like this one exist, let alone repeated scheduled runs. Authenticated
requests get 5000/hour, which effectively never gets hit by this project.
"""

import os
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

from constants import HEADERS

REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 3


@dataclass
class Asset:
    browser_download_url: str
    name: str


@dataclass
class GithubRelease:
    tag_name: str
    html_url: str
    body: str
    prerelease: bool
    assets: list[Asset]


def _auth_headers() -> dict:
    headers = dict(HEADERS)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers["X-GitHub-Api-Version"] = "2022-11-28"
    headers["Accept"] = "application/vnd.github+json"
    return headers


def github_get(url: str, params: dict | None = None) -> requests.Response:
    """GET a GitHub API URL with auth + retry on rate limiting/transient errors."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=_auth_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            last_exc = e
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code in (403, 429):
            # Could be rate limiting (secondary or primary). Respect
            # Retry-After / X-RateLimit-Reset when present, otherwise back off.
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
            else:
                wait = RETRY_BACKOFF_SECONDS * attempt
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0" or "rate limit" in response.text.lower():
                print(
                    f"GitHub API rate limited (attempt {attempt}/{MAX_RETRIES}), "
                    f"waiting {wait}s. Set GITHUB_TOKEN to raise the limit."
                )
                time.sleep(wait)
                continue
        return response

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Failed to reach {url} after {MAX_RETRIES} attempts")


def _to_github_release(release: dict) -> GithubRelease:
    assets = [
        Asset(browser_download_url=asset["browser_download_url"], name=asset["name"])
        for asset in release["assets"]
    ]

    return GithubRelease(
        tag_name=release["tag_name"],
        html_url=release["html_url"],
        body=release.get("body") or "",
        prerelease=bool(release.get("prerelease")),
        assets=assets,
    )


def _fetch_release(url: str) -> GithubRelease | None:
    response = github_get(url)

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return _to_github_release(response.json())


def get_release_by_tag(repo: str, tag: str) -> GithubRelease | None:
    encoded_tag = quote(tag, safe="")
    url = f"https://api.github.com/repos/{repo}/releases/tags/{encoded_tag}"
    return _fetch_release(url)


def get_last_build_version(repo: str) -> GithubRelease | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    return _fetch_release(url)


def list_releases(repo: str, per_page: int = 100) -> list[GithubRelease]:
    """List releases for a repo, newest first (GitHub's own default order)."""
    url = f"https://api.github.com/repos/{repo}/releases"
    response = github_get(url, params={"per_page": per_page})
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return [_to_github_release(r) for r in response.json()]


def get_latest_release_for_tag_prefix(repo: str, tag_prefix: str) -> GithubRelease | None:
    """Find the newest release in `repo` whose tag starts with `tag_prefix`.

    Used so multiple apps can be released from the same repo (tags like
    'youtube-21.04.223', 'instagram-435.0.0.37.76', ...) while each app still
    correctly detects its own "did we already build this version" state.
    """
    for release in list_releases(repo):
        if release.tag_name.startswith(tag_prefix):
            return release
    return None
