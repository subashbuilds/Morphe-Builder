"""Figures out which app version to download+patch, for ANY app/patches combo.

This replaces the old approach of regex-matching a specific patch dev's
changelog wording (which only worked for one dev's phrasing and broke the
moment they wrote "Bump support for" instead of "Add support for" -- both
appear in crimera/piko's real changelog history).

Instead this shells out to the Morphe CLI's own `list-versions` command,
which reports -- straight from the patches file itself -- which app
version(s) its patches currently target best. This works identically for
any Morphe/ReVanced-style patches bundle from any developer, not just one.

Verified against real output from real patch bundles, e.g.:

    $ java -jar morphe-desktop.jar list-versions \\
          --patches patches.mpp -f com.google.android.youtube
    INFO: Package name: com.google.android.youtube
    Most common compatible versions:
        21.04.223 (74 patches)
        20.51.39 (74 patches)
        20.31.42 (74 patches)
        20.21.37 (74 patches)
"""

import re
import subprocess

_VERSION_LINE = re.compile(r"^(\S+)(?:\s+\[.*\])?\s+\((\d+) patches?\)\s*$")


class ListVersionsError(Exception):
    pass


def _run_cli(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ListVersionsError(
            f"Command failed ({' '.join(command)}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Numeric sort key so "21.04.223" > "20.51.39" even though they don't
    have the same number of dot-separated parts."""
    return tuple(int(d) for d in re.findall(r"\d+", version))


def get_supported_versions(
    cli_jar: str,
    patches_files: list[str],
    package_name: str,
    include_experimental: bool = False,
) -> list[str]:
    """Ask the CLI which app version(s) the given patches best support.

    Returns versions newest/best-first, or an empty list if the patches
    place no restriction on version for this package (some patch sets are
    compatible with "any" version) -- callers should fall back to
    APKMirror's own version listing in that case.

    Ranking: the CLI's own "(N patches)" compatibility count is the primary
    sort key (most-compatible first, exactly what "most common compatible
    versions" is telling us), and version number descending is used only as
    a tiebreaker between equally-compatible versions. We sort explicitly by
    both rather than trusting the CLI's raw print order for ties, since
    that ordering isn't a documented guarantee -- version numbers are
    parsed and compared numerically (not as plain strings), so "21.04.223"
    correctly ranks above "20.51.39" or "9.15.51" regardless of digit count.
    """
    command = ["java", "-jar", cli_jar, "list-versions"]
    for pf in patches_files:
        command.extend(["--patches", pf])
    command.extend(["-f", package_name])
    if include_experimental:
        command.append("--include-experimental")

    output = _run_cli(command)

    entries: list[tuple[str, int]] = []
    for line in output.splitlines():
        match = _VERSION_LINE.match(line.strip())
        if match:
            entries.append((match.group(1), int(match.group(2))))

    entries.sort(key=lambda e: (e[1], _version_sort_key(e[0])), reverse=True)
    return [version for version, _patch_count in entries]
