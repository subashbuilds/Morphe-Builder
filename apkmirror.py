"""Generic APKMirror scraping client.

Made app-agnostic: every function takes a fully-built URL (from
AppConfig.apkmirror_listing_url() / apkmirror_release_url()) instead of
assuming "instagram". The same code now downloads YouTube, YouTube Music,
Instagram, or anything else added to config.yml.

I verified the real page structure against live APKMirror pages before
touching this file (a specific release page + an app's main listing page).
The variant table's column order the original code relied on
(cells[0]=version/badge, cells[1]=architecture, cells[2]=min OS, cells[3]=DPI)
and the "BUNDLE" text badge are both confirmed correct, so those selectors
are kept as-is rather than replaced with guesses. Only the parts that are
provably wrong are changed:

  * Every `.string.strip()` call -> `.get_text(strip=True)`. `.string`
    returns None (not the visible text) the moment a tag has more than one
    child node -- e.g. a nested <span> -- which crashes with
    "AttributeError: 'NoneType' object has no attribute 'strip'". This is
    the exact kind of thing that breaks silently after a small markup tweak.
  * "Failed to find X" cases now raise immediately with context instead of
    print()-ing and letting a `None` blow up a few lines later.
  * Variant selection is explicit: the configured architecture and optional
    DPI are matched against APKMirror's columns, with universal/neutral-DPI
    fallbacks. This avoids downloading an unrelated first row just because it
    happened to appear first on the page.
"""

import re
from dataclasses import dataclass
from typing import cast

from bs4 import BeautifulSoup, Tag

from utils import FlareSolverrSession, download, flaresolverr_request


@dataclass
class Version:
    version: str
    link: str


@dataclass
class Variant:
    is_bundle: bool
    link: str
    architecture: str
    dpi: str = ""


class FailedToFindElement(Exception):
    def __init__(self, message: str | None = None) -> None:
        self.message = (
            f"Failed to find element{' ' + message if message is not None else ''}"
        )
        super().__init__(self.message)


class FailedToFetch(Exception):
    def __init__(self, url: str | None = None) -> None:
        self.message = f"Failed to fetch{' ' + url if url is not None else ''}"
        super().__init__(self.message)


def _text(tag: Tag | None) -> str | None:
    """Return visible text even when an APKMirror tag has nested children."""
    if tag is None:
        return None
    text = tag.get_text(strip=True)
    return text or None


_KNOWN_ARCHITECTURES = (
    "armeabi-v7a",
    "arm64-v8a",
    "universal",
    "x86_64",
    "x86",
)
_DPI_PATTERN = re.compile(
    r"(?:\d{2,4}\s*-\s*)?\d{2,4}\s*dpi|anydpi|nodpi", re.IGNORECASE
)


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    compact = re.sub(r"[-_\s]+", "", normalized)
    # Compare canonical tokens so x86_64 can never collapse into x86 while
    # still accepting APKMirror's occasional x86-64 spelling.
    for architecture in _KNOWN_ARCHITECTURES:
        canonical = re.sub(r"[-_\s]+", "", architecture)
        if canonical in compact:
            return architecture
    return normalized


def _find_dpi(values: list[str]) -> str:
    for value in values:
        match = _DPI_PATTERN.search(value)
        if match:
            return match.group(0).replace(" ", "").lower()
    return ""


def _is_neutral_dpi(value: str) -> bool:
    normalized = value.lower()
    return "anydpi" in normalized or "nodpi" in normalized


def _dpi_matches_requested(value: str, requested: str | None) -> bool:
    if not requested:
        return False
    if requested in {"anydpi", "nodpi"}:
        return _is_neutral_dpi(value)

    wanted = int(requested)
    numbers = [int(number) for number in re.findall(r"\d+", value)]
    if not numbers:
        return False
    # APKMirror commonly uses ranges such as "120-480dpi". A requested 480
    # is supported by that bundle, so do not discard it as an inexact match.
    return min(numbers) <= wanted <= max(numbers)


def get_versions(listing_url: str, session: FlareSolverrSession) -> list[Version]:
    """Get the "all versions" list from an app's main APKMirror page.

    This is a fallback path only used when the patches bundle doesn't
    restrict which app version is needed (see version_resolver.py) -- the
    primary, more reliable way this project picks a version is asking the
    Morphe CLI itself via `list-versions`.
    """
    response = flaresolverr_request(listing_url, session=session)
    if response.status_code != 200:
        raise FailedToFetch(f"{listing_url}: {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")
    list_widget = soup.find("div", attrs={"class": "listWidget"})

    out: list[Version] = []
    if list_widget is None:
        return out

    rows = cast(Tag, list_widget).find_all("div", recursive=False)[1:]
    for row in rows:
        version_tag = row.find("span", attrs={"class": "infoSlide-value"})
        version = _text(version_tag)
        link_tag = row.find("a")
        if version is None or link_tag is None or not link_tag.get("href"):
            continue

        link = f"https://www.apkmirror.com{link_tag['href']}"
        out.append(Version(version=version, link=link))

    return out


def get_variants(release_url: str, session: FlareSolverrSession) -> list[Variant]:
    """Get every downloadable variant listed on a specific release's page."""
    variants_page = flaresolverr_request(release_url, session=session)
    if variants_page is None or variants_page.status_code != 200:
        raise FailedToFetch(release_url)

    soup = BeautifulSoup(variants_page.content, "html.parser")

    variants_table = soup.find("div", attrs={"class": "table"})
    if variants_table is None:
        raise FailedToFindElement(f"variants table on {release_url}")

    # First direct child is the header row ("Variant / Arch / Version / DPI");
    # skip it, same as the rest of the table's actual data rows.
    rows = cast(Tag, variants_table).find_all("div", recursive=False)[1:]

    variants: list[Variant] = []
    for row in rows:
        cells = row.find_all("div", attrs={"class": "table-cell"}, recursive=False)
        if not cells:
            continue

        link_element = row.find("a", attrs={"class": "accent_color"})
        if link_element is None:
            continue

        is_bundle_tag = row.find("span", attrs={"class": "apkm-badge"})
        is_bundle = (_text(is_bundle_tag) or "").upper() == "BUNDLE"

        cell_texts = [_text(cell) or "" for cell in cells]
        architecture_text = cell_texts[1] if len(cell_texts) > 1 else ""
        architecture = _normalize_architecture(architecture_text) or "unknown"
        dpi = _find_dpi(cell_texts[2:])

        link = f"https://www.apkmirror.com{link_element.attrs['href']}"
        variants.append(
            Variant(
                is_bundle=is_bundle,
                link=link,
                architecture=architecture,
                dpi=dpi,
            )
        )

    return variants


def _dpi_rank(variant: Variant, requested_dpi: str | None) -> int:
    if requested_dpi:
        if _dpi_matches_requested(variant.dpi, requested_dpi):
            return 0
        if _is_neutral_dpi(variant.dpi):
            return 1
        return 2

    # With no DPI configured, prefer anydpi/nodpi, but keep a density-specific
    # same-architecture APK as a last resort so niche apps still build.
    return 0 if _is_neutral_dpi(variant.dpi) else 1


def select_variant(
    variants: list[Variant],
    architecture: str,
    dpi: str | None = None,
    *,
    prefer_bundle: bool = False,
    bundle_only: bool = False,
) -> Variant:
    """Select the smallest safe APKMirror input for one configured output.

    Preference order for a specific architecture is exact architecture + best
    DPI, then a universal fallback. A requested numeric DPI also matches a
    range (for example 120-480dpi satisfies 480), and falls back to
    anydpi/nodpi. For APK-only builds, a plain exact APK is preferred over a
    bundle because it avoids carrying unrelated density/native splits.
    Module builds require a bundle so the stock install can remain complete.
    """
    wanted_arch = _normalize_architecture(architecture)
    available = [v for v in variants if not bundle_only or v.is_bundle]
    exact = [v for v in available if v.architecture == wanted_arch]
    universal = [v for v in available if v.architecture == "universal"]
    candidates = exact if wanted_arch == "universal" else exact + universal

    if not candidates:
        raise FailedToFindElement(
            f"{architecture} or universal downloadable variant"
        )

    def rank(variant: Variant) -> tuple[int, int]:
        is_exact = variant.architecture == wanted_arch
        is_universal = variant.architecture == "universal"
        if dpi:
            if is_exact and _dpi_matches_requested(variant.dpi, dpi):
                match_rank = 0
            elif is_exact and _is_neutral_dpi(variant.dpi):
                match_rank = 1
            elif is_universal and _dpi_matches_requested(variant.dpi, dpi):
                match_rank = 2
            elif is_universal and _is_neutral_dpi(variant.dpi):
                match_rank = 3
            elif is_exact:
                match_rank = 4
            else:
                match_rank = 5
        else:
            if is_exact and _is_neutral_dpi(variant.dpi):
                match_rank = 0
            elif is_universal and _is_neutral_dpi(variant.dpi):
                match_rank = 1
            elif is_exact:
                match_rank = 2
            else:
                match_rank = 3
        format_rank = 0 if variant.is_bundle == prefer_bundle else 1
        return match_rank, format_rank

    selected = min(candidates, key=rank)
    if selected.architecture != wanted_arch:
        print(
            f"No {wanted_arch} variant is available; using universal instead."
        )
    if _dpi_rank(selected, dpi) > 0:
        requested = dpi or "anydpi/nodpi"
        print(
            f"No exact {requested} variant is available for {wanted_arch}; "
            f"using {selected.dpi or 'the available density'}."
        )
    return selected


def get_bundle_variant(
    release_url: str,
    session: FlareSolverrSession,
    preferred_architectures: list[str] | None = None,
    dpi: str | None = None,
) -> Variant:
    """Compatibility wrapper returning one explicit architecture's bundle."""
    architecture = (preferred_architectures or ["universal"])[0]
    variants = get_variants(release_url, session=session)
    return select_variant(
        variants, architecture, dpi=dpi, prefer_bundle=True, bundle_only=True
    )


def download_apk(variant: Variant, path: str, session: FlareSolverrSession) -> None:
    """Download the APK/APKM file behind a variant's page link."""
    response = flaresolverr_request(variant.link, session=session)
    if response.status_code != 200:
        raise FailedToFetch(variant.link)

    page = BeautifulSoup(response.content, "html.parser")

    download_button = page.find("a", attrs={"class": "downloadButton"})
    if download_button is None:
        raise FailedToFindElement(f"download button on {variant.link}")

    download_page_link = (
        f"https://www.apkmirror.com{cast(Tag, download_button).attrs['href']}"
    )

    download_page = flaresolverr_request(
        download_page_link, session=session, return_cookies=True
    )
    if download_page.status_code != 200:
        raise FailedToFetch(download_page_link)

    download_page_body = BeautifulSoup(download_page.content, "html.parser")

    direct_link = download_page_body.find("a", attrs={"rel": "nofollow"})
    if direct_link is None:
        raise FailedToFindElement(f"direct download link on {download_page_link}")

    direct_link_href = cast(Tag, direct_link).attrs["href"]
    direct_link_url = f"https://www.apkmirror.com{direct_link_href}"
    print(f"Direct link: {direct_link_url}")

    download(
        direct_link_url,
        path,
        headers={
            "Referer": download_page_link,
            "User-Agent": download_page.headers.get("User-Agent"),
        },
        cookies=download_page.cookies,
    )
