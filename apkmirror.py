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
  * Added get_bundle_variant(): we only need ONE variant per version now
    (the combined .apkm bundle) because the CLI's --striplibs flag derives
    every requested architecture from that single download. This removes
    the old per-architecture scrape/download loop, which was the biggest
    source of "worked for one arch, silently skipped another" failures.
"""

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
    """Safe replacement for the old `tag.string.strip()` pattern.

    `.string` is None whenever a tag has anything other than exactly one
    text-node child (e.g. a nested <span> or an inline ad comment), which is
    common on APKMirror and was the cause of several latent crashes.
    `.get_text(strip=True)` reliably returns the visible text regardless of
    how deeply it's nested.
    """
    if tag is None:
        return None
    text = tag.get_text(strip=True)
    return text or None


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

        # cells[1] is the Architecture column -- confirmed against a live
        # APKMirror release page (columns are Variant/Arch/Version/DPI).
        architecture = _text(cells[1]) if len(cells) > 1 else None
        architecture = architecture or "unknown"

        link = f"https://www.apkmirror.com{link_element.attrs['href']}"
        variants.append(
            Variant(is_bundle=is_bundle, link=link, architecture=architecture)
        )

    return variants


def get_bundle_variant(release_url: str, session: FlareSolverrSession) -> Variant:
    """Get the single combined "bundle" (.apkm) variant for a release.

    Only one download is needed per version: the CLI's --striplibs flag
    derives every architecture-specific output from this one bundle, so we
    no longer scrape/download once per architecture (see patch_runner.py).
    """
    variants = get_variants(release_url, session=session)
    bundle = next((v for v in variants if v.is_bundle), None)
    if bundle is not None:
        return bundle

    # Very old/simple releases sometimes only ever had a single plain APK
    # (no split configs, so no "BUNDLE" badge at all) -- fall back to it.
    if variants:
        return variants[0]

    raise FailedToFindElement(f"any downloadable variant on {release_url}")


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
