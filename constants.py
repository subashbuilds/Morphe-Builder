import os


def get_repo() -> str:
    """The 'owner/repo' this workflow is running in (e.g. GITHUB_REPOSITORY).

    BUGFIX: this used to raise EnvironmentError at *import time* if unset,
    which meant `python main.py --help`, unit tests, or any local/offline use
    of this codebase would crash immediately, even when the repo value isn't
    actually needed yet. It's now only required at the point it's actually
    used (publishing a release / reporting to Telegram).
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise EnvironmentError(
            "GITHUB_REPOSITORY is not set (this is set automatically inside "
            "GitHub Actions; set it manually if running locally, e.g. "
            "GITHUB_REPOSITORY=you/your-repo)"
        )
    return repo


# Browser-like headers for talking to APKMirror (which fronts Cloudflare and
# will reject requests that don't look like a real browser).
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-GB,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "priority": "u=0, i",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}

BINS_DIR = "bins"
OUTPUT_DIR = "output"
