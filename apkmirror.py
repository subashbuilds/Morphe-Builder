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
    # How many "split APKs" APKMirror advertises for this row (0 = not
    # stated). A smaller split count is a smaller download for the same
    # architecture + DPI, so single-source APK builds prefer the row with
    # the fewest splits -- e.g. an "arm64-v8a 480dpi, 2 splits" upload wins
    # over a "480-640dpi, 10 splits" one.
    split_count: int = 0
    # Advertised download size in bytes (0 = not stated / not probed yet).
    # This is the primary "smallest upload" signal: among rows matching the
    # requested architecture and DPI (exact or a range containing it), the
    # smallest file wins -- e.g. a 140 MB "320-480dpi, 1 S" bundle beats a
    # 278 MB "480dpi, 10 S" one. Split count is only a fallback for rows
    # whose size APKMirror does not state.
    file_size: int = 0
    # The row's own label, e.g. "18.5.4.993608717-release-arm64-v8a". One
    # APKMirror release page can list stable ("release") and pre-release
    # ("beta"/"alpha") uploads side by side with the same architecture and
    # DPI, so the label is what tells them apart.
    name: str = ""
    # True for beta/alpha/dev/canary/nightly rows. Never chosen while a
    # stable row for the same release exists.
    prerelease: bool = False


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
_SPLIT_PATTERN = re.compile(r"(\d+)\s*split", re.IGNORECASE)
_DPI_PATTERN = re.compile(
    r"(?:\d{2,4}\s*-\s*)?\d{2,4}\s*dpi|anydpi|nodpi", re.IGNORECASE
)
# APKMirror shows sizes like "278 MB", "140.5 MB", "1.2 GB" or "850 KB".
# No \b after the unit: get_text(strip=True) concatenates sibling nodes
# without a separator, so a cell can read "278MB" or "278MB(10splits)".
_SIZE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(KB|MB|GB)(?![a-z])", re.IGNORECASE
)
_SIZE_MULTIPLIERS = {"kb": 1024, "mb": 1024**2, "gb": 1024**3}


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


_PRERELEASE_PATTERN = re.compile(
    r"(?<![a-z0-9])(beta|alpha|dev|canary|nightly|preview)(?![a-z0-9])",
    re.IGNORECASE,
)


def _is_prerelease(*values: str) -> bool:
    """True when a row label / URL marks a beta, alpha, dev or canary build.

    Boundaries are non-alphanumerics, so "gboard-...-beta-arm64-v8a" and
    "18.5.4-beta" match while a name merely containing the letters does not.
    """
    return any(_PRERELEASE_PATTERN.search(value) for value in values if value)


_CHANNEL_TOKENS = r"release|beta|alpha|dev|canary|nightly|preview"
_VERSION_CHANNEL_PATTERN = re.compile(
    rf"-({_CHANNEL_TOKENS})(?:-(?:arm64[-_]v8a|armeabi[-_]v7a|x86[-_]64|x86))?$",
    re.IGNORECASE,
)


def version_channel(version: str) -> str | None:
    """The release channel a patches-reported version names, if any.

    Some patch sets report the app's full Android versionName, e.g.
    "18.5.4.993608717-release-arm64-v8a" for Gboard. The "-release" /
    "-beta" part is part of that version: APKMirror lists
    "...-release-arm64-v8a" and "...-beta-arm64-v8a" as separate rows with
    different versionNames, and the patches target only one of them.
    Returns "release", "beta", ... (lowercase) or None for a plain version.
    Only these known tokens are recognised, so versions such as X's
    "12.7.1-release.0" are untouched.
    """
    match = _VERSION_CHANNEL_PATTERN.search(version.strip())
    return match.group(1).lower() if match else None


def _has_channel(variant: "Variant", channel: str) -> bool:
    pattern = re.compile(
        rf"(?<![a-z0-9]){re.escape(channel)}(?![a-z0-9])", re.IGNORECASE
    )
    return bool(pattern.search(variant.name) or pattern.search(variant.link))


def _narrow_to_channel(
    variants: list["Variant"], channel: str | None
) -> list["Variant"]:
    """Keep the rows of the channel the patches target.

    With a channel ("release"), only rows labelled with it compete. When no
    row carries that label (APKMirror did not tag the row) or no channel is
    known, fall back to every row minus pre-releases (_drop_prereleases).
    A pre-release channel that was explicitly requested ("beta") is honoured.
    """
    if channel:
        matching = [v for v in variants if _has_channel(v, channel)]
        if matching:
            return matching
    return _drop_prereleases(variants)


def _find_split_count(values: list[str]) -> int:
    """Read APKMirror's "(N splits)" note for a variant row, if present."""
    for value in values:
        match = _SPLIT_PATTERN.search(value)
        if match:
            return int(match.group(1))
    return 0


def _find_file_size(values: list[str]) -> int:
    """Read APKMirror's advertised download size ("278 MB") as bytes.

    Returns 0 when the row states no size. The size cell is the most
    reliable "smallest upload" signal available without downloading, so
    selection compares it before falling back to split count.

    The size is NOT in the variant table's own cells: APKMirror puts it
    in the row's download link text (e.g. "Download (278 MB)"), so
    callers must pass the link text in `values` too. Without it every
    row reports 0 and selection silently falls back to split count --
    which is why the 278 MB exact-480dpi row kept winning over the
    140 MB 320-480dpi one.
    """
    for value in values:
        match = _SIZE_PATTERN.search(value)
        if match:
            amount = float(match.group(1))
            unit = match.group(2).lower()
            return int(amount * _SIZE_MULTIPLIERS[unit])
    return 0


def _format_size(size_bytes: int) -> str:
    if not size_bytes:
        return "unknown size"
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.1f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"


def _find_dpi(values: list[str]) -> str:
    for value in values:
        match = _DPI_PATTERN.search(value)
        if match:
            return match.group(0).replace(" ", "").lower()
    return ""


def _is_neutral_dpi(value: str) -> bool:
    normalized = value.lower()
    return "anydpi" in normalized or "nodpi" in normalized


def _dpi_match_rank(value: str, requested: str) -> int:
    """How well `value` satisfies `requested`: 0 = exact, 1 = range, 2 = none.

    Only a tiebreak, and only at EQUAL split counts: the split count decides
    first (a 2-split "480-640dpi" row is a smaller download than a 10-split
    exact "480dpi" row), so this merely prefers the exact density when the
    two uploads are otherwise the same size.
    """
    if _is_any_dpi(value) or not value:
        return 2
    wanted = str(requested).strip().lower().removesuffix("dpi")
    actual = value.strip().lower().removesuffix("dpi")
    if actual == wanted:
        return 0
    return 1 if _dpi_matches_requested(value, requested) else 2


def _dpi_compat(value: str, requested: str | None) -> int:
    """Grouping used for culling, not ordering: 0 = same density as
    requested (exact or a range containing it), 1 = neutral anydpi/nodpi,
    2 = a density the request says nothing about."""
    if not requested:
        return 0 if _is_neutral_dpi(value) else 1
    if _dpi_matches_requested(value, requested):
        return 0
    return 1 if _is_neutral_dpi(value) else 2


def _dpi_width(value: str) -> int:
    """Width of a density range ("360-480dpi" -> 120); 0 for a single value.

    Used only to break ties: for the same split count, the narrower range
    (e.g. 480-640dpi over 120-640dpi) is the smaller upload.
    """
    numbers = [int(number) for number in re.findall(r"\d+", value)]
    if len(numbers) < 2:
        return 0
    return max(numbers) - min(numbers)


def _is_any_dpi(value: str) -> bool:
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
        split_count = _find_split_count(cell_texts)
        # The advertised size lives in the download link's text
        # ("Download (278 MB)"), not in a table cell -- include it, or
        # every row reports size 0 and smallest-size selection never
        # engages. The link text is also a split-count source.
        link_text = _text(link_element) or ""
        file_size = _find_file_size([*cell_texts, link_text])
        if not split_count:
            split_count = _find_split_count([link_text])

        href = link_element.attrs["href"]
        link = f"https://www.apkmirror.com{href}"
        # Same-page stable/beta uploads differ only in their label, which can
        # appear in the link text, the first cell or the URL slug.
        name = link_text or (cell_texts[0] if cell_texts else "")
        prerelease = _is_prerelease(link_text, href, *cell_texts)
        variants.append(
            Variant(
                is_bundle=is_bundle,
                link=link,
                architecture=architecture,
                dpi=dpi,
                split_count=split_count,
                file_size=file_size,
                name=name,
                prerelease=prerelease,
            )
        )

    return variants


_BYTES_PATTERN = re.compile(r"\(\s*([\d,]{4,})\s*bytes?\s*\)", re.IGNORECASE)
_FILE_SIZE_PATTERN = re.compile(
    r"file\s*size\s*:?\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB)(?![a-z])", re.IGNORECASE
)


def parse_variant_page_size(page: BeautifulSoup) -> int:
    """Read a variant's real download size (bytes) from its own page.

    The release page's variant table states NO size (confirmed from CI
    logs: every candidate reported "unknown size"), so the size has to be
    read from each variant's page. Sources, most precise first:
      1. an exact byte count, "File size: 278.2 MB (291,737,613 bytes)"
      2. the "File size: 278.2 MB" spec row
      3. the download button text, "Download APK Bundle (278.2 MB)"
    Returns 0 when none of them is found.
    """
    text = page.get_text(" ", strip=True)

    match = _BYTES_PATTERN.search(text)
    if match:
        return int(match.group(1).replace(",", ""))

    match = _FILE_SIZE_PATTERN.search(text)
    if match:
        return int(float(match.group(1)) * _SIZE_MULTIPLIERS[match.group(2).lower()])

    button = page.find("a", attrs={"class": "downloadButton"})
    if button is not None:
        return _find_file_size([_text(button) or ""])
    return 0


def _drop_prereleases(variants: list[Variant]) -> list[Variant]:
    """Remove beta/alpha rows, unless nothing but pre-releases is left."""
    stable = [v for v in variants if not v.prerelease]
    return stable or variants


# Upper bound on variant pages fetched per app/architecture. Release pages
# list roughly 5-15 variants; this only guards against a pathological page.
MAX_SIZE_PROBES = 16


def populate_variant_sizes(
    variants: list[Variant],
    architecture: str,
    dpi: str | None,
    session: FlareSolverrSession,
    *,
    bundle_only: bool = False,
    channel: str | None = None,
) -> None:
    """Fill in `file_size` for the variants that could actually be selected.

    Only rows that survive the same architecture and density filtering that
    select_variant() applies first are fetched -- a row that can never win
    is not worth a request. Rows whose size is already known are skipped,
    so a variant shared by several architectures is probed once. A failed
    probe leaves that row at size 0 (ranked last among sized rows).
    """
    wanted_arch = _normalize_architecture(architecture)
    candidates = [
        v
        for v in variants
        if (not bundle_only or v.is_bundle)
        and v.architecture in (wanted_arch, "universal")
    ]
    if not candidates:
        return
    candidates = _narrow_to_channel(candidates, channel)

    def density_class(variant: Variant) -> int:
        if dpi:
            return _dpi_compat(variant.dpi, dpi)
        return 0 if _is_neutral_dpi(variant.dpi) else 1

    # Density class outranks size in select_variant(), so only the best
    # density class present can ever be chosen.
    best_class = min(density_class(v) for v in candidates)
    to_probe = [
        v for v in candidates if density_class(v) == best_class and not v.file_size
    ][:MAX_SIZE_PROBES]

    for variant in to_probe:
        try:
            response = flaresolverr_request(variant.link, session=session)
            if response is None or response.status_code != 200:
                raise FailedToFetch(variant.link)
            variant.file_size = parse_variant_page_size(
                BeautifulSoup(response.content, "html.parser")
            )
        except Exception as e:  # one bad probe must not fail the build
            print(f"Could not read the size of {variant.link}: {e}")
            continue
        if not variant.file_size:
            print(f"No file size found on {variant.link}")


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
    channel: str | None = None,
) -> Variant:
    """Select the smallest safe APKMirror input for one configured output.

    Order of decisions:

    1. Density class -- a row that uses the requested density (exact
       "480dpi", or a range containing it such as "480-640dpi" or
       "360-480dpi") beats anydpi/nodpi, which beats an unrelated density.
    2. Architecture -- the requested architecture wins, then "universal".
    3. Smallest upload -- the row with the smallest advertised file size
       wins (a 140 MB "320-480dpi, 1 S" bundle beats a 278 MB "480dpi,
       10 S" one), because the smallest file is the smallest download and
       install. Rows with no stated size fall back to fewest splits
       (2 splits beats 10 splits). Ties go to an exact DPI over a range,
       then to the plain APK/bundle the build mode wants, then to the
       narrowest density range.
    """
    wanted_arch = _normalize_architecture(architecture)
    candidates = [v for v in variants if not bundle_only or v.is_bundle]

    def arch_rank(variant: Variant) -> int:
        if variant.architecture == wanted_arch:
            return 0
        return 1 if variant.architecture == "universal" else 2

    candidates = [v for v in candidates if arch_rank(v) < 2]
    if not candidates:
        raise FailedToFindElement(
            f"{architecture} or universal downloadable variant"
        )
    # One release page can list several uploads that differ only in their
    # versionName channel -- Gboard's "18.5.4.993608717-release-arm64-v8a"
    # and "18.5.4.993608717-beta-arm64-v8a" -- and the patches target one of
    # them. Keep the targeted channel's rows (or, with no channel known,
    # everything except pre-releases).
    narrowed = _narrow_to_channel(candidates, channel)
    if len(narrowed) < len(candidates):
        skipped = [v.name or v.link for v in candidates if v not in narrowed]
        print(
            f"Ignoring variant(s) outside the targeted "
            f"'{channel or 'stable'}' channel: {', '.join(skipped)}"
        )
    elif channel and not any(_has_channel(v, channel) for v in candidates):
        print(
            f"WARNING: no variant is labelled '{channel}'; "
            "choosing among the stable variants instead."
        )
    candidates = narrowed

    def density_class(variant: Variant) -> int:
        if dpi:
            return _dpi_compat(variant.dpi, dpi)
        return 0 if _is_neutral_dpi(variant.dpi) else 1

    def rank(variant: Variant) -> tuple[int, int, int, int, int, int, int]:
        # 1. Same density as requested (exact or range) > anydpi/nodpi >
        #    an unrelated density.
        # 2. The requested architecture > universal.
        # 3. SMALLEST advertised file size wins: a 140 MB "320-480dpi,
        #    1 S" bundle is a smaller download and install than a 278 MB
        #    "480dpi, 10 S" one. 0 = size not stated, ranked after every
        #    stated size so an unstated row never beats a known-small one.
        size_rank = variant.file_size if variant.file_size else float("inf")
        # 4. Equal size (or no sizes stated): FEWEST splits wins, for the
        #    same reason. 0 = count not stated, ranked between 1 and 2.
        split_rank = {0: 1}.get(variant.split_count, variant.split_count)
        # 5. Equal size and splits: exact DPI > a range containing it.
        quality = _dpi_match_rank(variant.dpi, dpi) if dpi else 0
        # 6. Then the plain APK / bundle the build mode wants.
        format_rank = 0 if variant.is_bundle == prefer_bundle else 1
        # 7. Then the narrowest density range (smaller upload on ties).
        return (
            density_class(variant),
            arch_rank(variant),
            size_rank,
            split_rank,
            quality,
            format_rank,
            _dpi_width(variant.dpi),
        )

    selected = min(candidates, key=rank)
    # Never pick silently when the size signal is missing: if no
    # candidate states a size, smallest-size selection cannot work and
    # the pick falls back to split count -- say so loudly, with the
    # candidates, so a wrong pick is diagnosable from the log.
    if not any(v.file_size for v in candidates):
        print(
            "WARNING: APKMirror stated no file size for any candidate; "
            "smallest-size selection is disabled and split count decides."
        )
        for variant in sorted(candidates, key=rank)[:8]:
            print(
                f"  candidate: {variant.architecture}/"
                f"{variant.dpi or 'unknown DPI'} "
                f"{_format_size(variant.file_size)} "
                f"splits={variant.split_count or '?'} "
                f"{'bundle' if variant.is_bundle else 'apk'}"
            )
    if selected.architecture != wanted_arch:
        print(
            f"No {wanted_arch} variant is available; using universal instead."
        )
    if dpi and _dpi_match_rank(selected.dpi, dpi) > 0:
        print(
            f"No exact {dpi}dpi variant is available for {wanted_arch}; "
            f"using {selected.dpi or 'the available density'}."
        )
        # Show what APKMirror actually listed, so a surprising pick is easy
        # to explain: every fallback row is printed with its size and
        # split count.
        for variant in sorted(candidates, key=rank)[:8]:
            print(
                f"  candidate: {variant.architecture}/"
                f"{variant.dpi or 'unknown DPI'} "
                f"{_format_size(variant.file_size)} "
                f"splits={variant.split_count or '?'} "
                f"{'bundle' if variant.is_bundle else 'apk'}"
            )
    print(
        f"Selected the {selected.architecture}/{selected.dpi or 'unknown DPI'} "
        f"variant ({_format_size(selected.file_size)}"
        f"{f', {selected.split_count} split(s)' if selected.split_count else ''}) "
        f"(smallest match)."
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
