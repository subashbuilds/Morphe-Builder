<h2 align="center">Morphe Builder</h2>

<p align="center">
<img src="https://img.shields.io/github/actions/workflow/status/Subashbuilds/Morphe-Builder/.github%2Fworkflows%2Fbuild.yaml">
<a href="https://github.com/subashbuilds/Morphe-Builder/releases/" target="_blank">
  <img src="https://img.shields.io/badge/Github-Releases-blue?logo=Github" alt="Badge Alt Text">
</a>
  
</p>

Automatically build [Morphe](https://github.com/MorpheApp)/ReVanced-style patched
APKs for **any number of apps**, from **any patches source**, on a schedule,
using GitHub Actions.

Ships pre-configured for **YouTube**, **YouTube Music**, and **Instagram** —
but adding another app, or another developer's patches, is a config edit,
not a code change.

---

## Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuring apps](#configuring-apps)
  - [Config reference](#config-reference)
  - [Adding a new app](#adding-a-new-app)
- [Workflows](#workflows)
- [Running locally](#running-locally)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)
- [Disclaimer](#disclaimer)

---

## How it works

For every app marked `enabled: true` in [`config.yml`](./config.yml), the
builder:

1. **Downloads** the Morphe CLI and the app's patches bundle from GitHub
   Releases (with support for tracking either the latest stable release or
   dev/pre-release builds).
2. **Asks the patches themselves** which app version they currently support
   best, using the CLI's own `list-versions` command — no fragile changelog
   scraping, no hardcoded version numbers.
3. **Downloads one combined APK bundle** from APKMirror for that version. If
   that specific version can't be found or downloaded, it automatically
   falls back to the next-best supported version instead of failing.
4. **Patches** the bundle once per configured architecture, producing a
   plain installable APK and/or an experimental root module, depending on
   your config.
5. **Publishes a GitHub release** for that app and version, and skips
   straight past apps that haven't changed since the last run — so the
   daily schedule doesn't spam you with duplicate releases.

## Quick start

1. Click **Use this template** (or fork this repo).
2. Go to **Settings → Actions → General → Workflow permissions** and select
   **"Read and write permissions"**. This lets the workflow publish
   releases on your behalf.
3. Open [`config.yml`](./config.yml) and enable/disable the apps you want —
   the defaults (YouTube, YouTube Music, Instagram) work out of the box.
4. Go to the **Actions** tab, open **"Build (all enabled apps)"**, and run
   it manually once to confirm everything works.
5. From then on, it runs automatically every day. Check the
   [**Releases**](../../releases) page for your builds.

That's it — no code to touch for normal use.

## Configuring apps

Everything app-specific lives in [`config.yml`](./config.yml). It's split
into `defaults` (used by any app that doesn't override them) and a list of
`apps`.

```yaml
defaults:
  architectures: ["arm64-v8a", "universal"]
  build_mode: "apk"
  cli:
    repo: "MorpheApp/morphe-desktop"
    asset_regex: '^morphe-desktop-.*-all\.jar$'
    channel: "latest"
  patches:
    asset_regex: '^patches.*\.(mpp|rvp)$'
    channel: "latest"

apps:
  - id: youtube
    enabled: true
    name: "YouTube"
    package_name: "com.google.android.youtube"
    apkmirror:
      org: "google-inc"
      app: "youtube"
    patches:
      repo: "MorpheApp/morphe-patches"
    build_mode: "both"
    architectures: ["arm64-v8a", "armeabi-v7a", "universal"]
    module:
      id: "morphe-youtube"
      mount_path: "auto"
```

### Config reference

| Field | Where | Description |
|---|---|---|
| `id` | app | Short, unique, lowercase id. Used in filenames and release tags. |
| `enabled` | app | Set to `false` to skip this app entirely without deleting it. |
| `name` | app | Human-readable name, used in release titles. |
| `package_name` | app | The Android package name (e.g. `com.google.android.youtube`). |
| `apkmirror.org` | app | The `<org>` segment of `apkmirror.com/apk/<org>/<app>/`. |
| `apkmirror.app` | app | The `<app>` segment of the same URL (the listing directory). |
| `apkmirror.release_prefix` | app, optional | Only needed if an app's release-page URLs use a different prefix than its listing slug (Instagram is one such case — see the comment in `config.yml`). Defaults to `apkmirror.app`. |
| `patches.repo` | app | `owner/repo` of the GitHub repo publishing the patches bundle. |
| `patches.channel` | app/defaults | `"latest"` (stable releases only) or `"dev"` (include pre-releases). |
| `cli.repo` / `cli.asset_regex` / `cli.channel` | app/defaults | Same idea, for the patcher CLI jar itself. Rarely needs overriding per app. |
| `build_mode` | app/defaults | `"apk"`, `"module"`, or `"both"`. See [below](#the-module-build-mode). |
| `architectures` | app/defaults | Any of `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`, `universal`. One APKMirror download is reused for all of them. |
| `module.id` / `module.mount_path` | app | Required when `build_mode` is `module`/`both`. See below. |
| `include_patches` / `exclude_patches` | app | Patch names to force on/off, matching `list-patches` output exactly. Leave empty to use the patch bundle's own defaults. |
| `version` | app, optional | Pin an exact version instead of auto-detecting one. |

### Adding a new app

Copy an existing block in `config.yml`, or uncomment the example at the
bottom of the file, and fill in:

- `package_name` — from the Play Store or the app's manifest.
- `apkmirror.org` / `apkmirror.app` — from the app's APKMirror URL:
  `apkmirror.com/apk/<org>/<app>/`.
- `patches.repo` — any GitHub repo that publishes a Morphe/ReVanced-style
  `.mpp`/`.rvp` patches bundle as a release asset. Not limited to
  MorpheApp's own patches — any compatible developer's patches work.

No Python code needs to change.

## Workflows

- **Build (all enabled apps)** — runs daily on a schedule, and can also be
  triggered manually (with an optional "force rebuild" checkbox).
- **Build (single app, manual)** — manually pick one app id, optionally pin
  an exact version, optionally force a rebuild even if that version was
  already released. Useful for testing a new app you just added.

## Running locally

Requires [uv](https://docs.astral.sh/uv/) and a JDK (21+).

```bash
uv sync

# Validate config.yml + patch/version resolution — no downloads, no
# FlareSolverr needed, completely side-effect free:
uv run main.py --check

# Build everything:
export GITHUB_REPOSITORY=you/your-repo
uv run main.py

# Build just one app:
uv run main.py --app youtube

# Pin an exact version:
uv run main.py --app youtube --version 21.04.223

# Rebuild even if already released:
uv run main.py --force
```

Actually downloading from APKMirror locally also needs a
[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) instance
running (`FLARESOLVERR_URL`, defaults to `http://localhost:8191`) — this is
handled automatically as a service container in the GitHub Actions
workflows.


## Troubleshooting

**A release didn't get updated even though I expected a new version.**
Check the workflow run's logs — the builder tries every version its patches
currently support, newest first, and only gives up after all of them fail.
The log will show exactly why each attempt was skipped (e.g. no matching
APKMirror release, no download variant, patch failure).

**"list-patches failed" / no candidate versions found.**
This usually means the patches bundle places no restriction on the app
version — the builder automatically falls back to APKMirror's own version
listing in that case, which is more fragile since it depends on APKMirror's
page structure. If it still fails, the app may need a `version:` pin in
config.yml as a temporary workaround.

**GitHub API rate limit errors.**
The builder authenticates every GitHub API call with `GITHUB_TOKEN`, which
GitHub Actions provides automatically — you shouldn't need to do anything.
If running locally, export a personal access token as `GITHUB_TOKEN` to get
the same higher rate limit.

**Telegram notifications aren't sending.**
They're optional. If `TG_TOKEN` isn't set as a repo secret, that step is
skipped automatically and never fails the build.

## Credits

- [Morphe](https://github.com/MorpheApp) — the patcher this project drives.
- [ReVanced](https://github.com/ReVanced) — the original patcher Morphe is
  based on.
- [crimera/piko](https://github.com/crimera/piko) — third-party
  Instagram/X patches used by the default config.
- [j-hc](https://github.com/j-hc) — this build pipeline's structure was
  inspired by j-hc's ReVanced/Morphe builder templates.

## Disclaimer

This repository builds and distributes **modified versions of third-party
apps** using publicly available patches. It is not affiliated with, and the
patched apps are not endorsed by, the original app developers. Use at your
own risk and in accordance with the terms of service of the apps you patch.
