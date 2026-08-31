import os
import subprocess
import sys
import uuid

import requests

from constants import get_repo
from github import get_last_build_version, get_release_by_tag

FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191")


class FlareSolverrSession:
    """Manages a FlareSolverr session for cookie reuse across multiple requests."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self._created = False

    def create(self) -> None:
        """Create a new session in FlareSolverr."""
        if self._created:
            return

        flaresolverr_endpoint = f"{FLARESOLVERR_URL}/v1"
        payload = {
            "cmd": "sessions.create",
            "session": self.session_id,
        }

        response = requests.post(flaresolverr_endpoint, json=payload, timeout=60)
        response.raise_for_status()

        result = response.json()
        if result.get("status") != "ok":
            raise RuntimeError(f"Failed to create FlareSolverr session: {result.get('message', 'Unknown error')}")

        self._created = True
        print(f"FlareSolverr session created: {self.session_id}")

    def destroy(self) -> None:
        """Destroy the session in FlareSolverr."""
        if not self._created:
            return

        flaresolverr_endpoint = f"{FLARESOLVERR_URL}/v1"
        payload = {
            "cmd": "sessions.destroy",
            "session": self.session_id,
        }

        try:
            response = requests.post(flaresolverr_endpoint, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "ok":
                print(f"FlareSolverr session destroyed: {self.session_id}")
        except Exception as e:
            print(f"Warning: Failed to destroy FlareSolverr session {self.session_id}: {e}")
        finally:
            self._created = False

    def __enter__(self):
        # Lazy on purpose: creating a session costs a real HTTP round-trip
        # to FlareSolverr. Some runs (e.g. `--check`, or every app having a
        # pinned version) never need to touch APKMirror at all, and
        # shouldn't be forced to have FlareSolverr running just to start.
        # flaresolverr_request() creates the session on first real use.
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.destroy()


def flaresolverr_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: dict | None = None,
    session: FlareSolverrSession | None = None,
    return_cookies: bool = False,
    max_retries: int = 3,
) -> requests.Response:
    """
    Make a request through FlareSolverr to bypass Cloudflare protection.

    Args:
        url: The URL to request
        method: HTTP method (GET or POST)
        headers: Optional headers to send
        data: Optional data for POST requests
        session: Optional FlareSolverrSession for cookie reuse
        return_cookies: If True, return cookies from the solution for use in subsequent requests
        max_retries: Retry transient FlareSolverr/network failures this many times

    Returns:
        requests.Response object with the response
    """
    flaresolverr_endpoint = f"{FLARESOLVERR_URL}/v1"

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }

    if session is not None:
        if not session._created:
            session.create()
        payload["session"] = session.session_id

    if headers:
        payload["headers"] = headers

    if method == "POST" and data:
        payload["postData"] = data

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            flaresolverr_response = requests.post(
                flaresolverr_endpoint, json=payload, timeout=120
            )
            flaresolverr_response.raise_for_status()
            result = flaresolverr_response.json()
            if result.get("status") != "ok":
                raise RuntimeError(f"FlareSolverr failed: {result.get('message', 'Unknown error')}")

            solution = result.get("solution")
            if solution is None:
                raise RuntimeError(f"FlareSolverr returned no solution: {result}")

            status_code = solution.get("status")
            if status_code is None:
                raise RuntimeError(f"FlareSolverr solution missing status: {solution}")

            # Create a fake Response object from FlareSolverr result
            response = requests.Response()
            response.status_code = status_code
            response._content = solution["response"]
            response.url = url
            response.headers.update({
                "User-Agent": solution.get("userAgent"),
                "CF-Turnstile-Token": solution.get("turnstile_token"),
            })

            if return_cookies and "cookies" in solution:
                for c in solution["cookies"]:
                    response.cookies.set(
                        name=c["name"],
                        value=c["value"],
                        domain=c["domain"],
                    )

            return response
        except (requests.RequestException, RuntimeError) as e:
            last_error = e
            if attempt < max_retries:
                print(f"FlareSolverr request to {url} failed (attempt {attempt}/{max_retries}): {e}")
            continue

    assert last_error is not None
    raise last_error


def panic(message: str):
    print(message, file=sys.stderr)
    sys.exit(1)


def send_message(message: str, token: str, chat_id: str, thread_id: str):
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    data = {
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
        "text": message,
        "message_thread_id": thread_id,
        "chat_id": chat_id,
    }

    response = requests.post(endpoint, data=data)
    response.raise_for_status()


def report_to_telegram(tag: str | None = None):
    tg_token = os.environ["TG_TOKEN"]
    tg_chat_id = os.environ["TG_CHAT_ID"]
    tg_thread_id = os.environ["TG_THREAD_ID"]

    repo = get_repo()
    release = get_release_by_tag(repo, tag) if tag else get_last_build_version(repo)

    if release is None and tag:
        raise RuntimeError(f"Could not fetch release for tag: {tag}")

    if release is None:
        raise RuntimeError("Could not fetch latest release")

    downloads = [
        f"[{asset.name}]({asset.browser_download_url})" for asset in release.assets
    ]

    message = f"""
[New Update Released !]({release.html_url})

▼ Downloads ▼

{"\n\n".join(downloads)}
"""

    print(message)

    send_message(message, tg_token, tg_chat_id, tg_thread_id)


def download(link, out, headers=None, cookies=None):
    dir_name = os.path.dirname(out)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    if os.path.exists(out):
        print(f"{out} already exists skipping download")
        return

    session = requests.Session()
    # https://www.slingacademy.com/article/python-requests-module-how-to-download-files-from-urls/#Streaming_Large_Files
    with session.get(link, stream=True, headers=headers, cookies=cookies) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)


def publish_release(
    tag: str, files: list[str], message: str, title: str = "", mark_latest: bool = False
):
    """Create (or update) a GitHub release and upload `files` to it.

    BUGFIX: the old version always passed `--latest`. That's fine for a
    single-app repo, but once a repo builds several apps under different
    tags (youtube-*, instagram-*, ...), forcing every single one of them to
    become "the latest release" just means whichever app happened to build
    last in a given run "wins" and misrepresents the others as stale. It's
    now opt-in per call; main.py leaves it off for multi-app runs.
    """
    key = os.environ.get("GITHUB_TOKEN")
    if key is None:
        raise Exception("GITHUB_TOKEN is not set")

    if len(files) == 0:
        raise Exception("Files should have atleast one item")

    command = ["gh", "release", "create", tag, "--notes", message, "--title", title]
    if mark_latest:
        command.append("--latest")

    command.extend(files)

    subprocess.run(command, env=os.environ.copy(), check=True)
