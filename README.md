<h1 align="center">Morphe Builder</h1>

<p align="center">
  <a href="https://github.com/subashbuilds/Morphe-Builder/actions/workflows/build.yaml" target="_blank">
    <img src="https://img.shields.io/github/actions/workflow/status/subashbuilds/Morphe-Builder/.github%2Fworkflows%2Fbuild.yaml?logo=github&label=build" alt="Build status">
  </a>
  <a href="https://github.com/subashbuilds/Morphe-Builder/releases/latest" target="_blank">
    <img src="https://img.shields.io/github/v/release/subashbuilds/Morphe-Builder?logo=github&label=latest%20release" alt="Latest release">
  </a>
  <a href="https://github.com/subashbuilds/Morphe-Builder/releases" target="_blank">
    <img src="https://img.shields.io/github/downloads/subashbuilds/Morphe-Builder/total?logo=android&label=downloads" alt="Total downloads">
  </a>
  <a href="./LICENSE" target="_blank">
    <img src="https://img.shields.io/github/license/subashbuilds/Morphe-Builder" alt="License">
  </a>
  <a href="https://github.com/subashbuilds/Morphe-Builder/commits/main" target="_blank">
    <img src="https://img.shields.io/github/last-commit/subashbuilds/Morphe-Builder" alt="Last commit">
  </a>
</p>

<p align="center">
Automatically build <a href="https://github.com/MorpheApp">Morphe</a>/ReVanced-style patched APKs
for <b>any number of apps</b>, from <b>any patches source</b>, on a schedule, using GitHub Actions.
</p>

<p align="center">
Ships pre-configured for <b>YouTube</b>, <b>YouTube Music</b>, and <b>Instagram</b> —
but adding another app, or another developer's patches, is a config edit, not a code change.
</p>

---

## Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuring apps](#configuring-apps)
  - [Config reference](#config-reference)
  - [Adding a new app](#adding-a-new-app)
- [Workflows](#workflows)
- [Running locally](#running-locally)
- [The "module" build mode](#the-module-build-mode)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## How it works

For every app marked `enabled: true` in [`config.yml`](./config.yml), the builder:

1. **Downloads** the Morphe CLI and the app's patches bundle from GitHub
   Releases, tracking either the latest stable release or dev/pre-release
   builds, per app.
2. **Asks the patches themselves** which app version they currently support
   best, using the CLI's own `list-versions` command — no changelog
   scraping, no hardcoded version numbers. Versions are ranked by how many
   patches actually support them, with version number only as a tiebreaker.
3. **Downloads one combined APK bundle** from APKMirror for that version.
   If that specific version can't be downloaded, it automatically falls
   back to the next-best supported version — but stops as soon as it hits a
   version that's already been released, rather than "falling through" to
   publish something older than what's already out.
4. **Patches** the bundle once per configured architecture, producing a
   plain installable APK and/or a Magisk/KernelSU root module, depending on
   your config — built as two genuinely separate patch runs so the module
   never gets the "GmsCore support" patch, which is only meant for
   non-rooted installs and actively conflicts with a root/mount install.
5. **Publishes a GitHub release** per app and version, and does nothing at
   all once you're already up to date — so the daily schedule doesn't spam
   you with duplicate or redundant releases.

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
      author: "subashbuilds"
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
| `module.id` | app | Required when `build_mode` is `module`/`both`. Must be unique across modules installed on a device. |
| `module.author` | app, optional | Shown as the module's author in Magisk/KernelSU. Defaults to a generic name if omitted. |
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

## The "module" build mode

Morphe's own CLI doesn't produce Magisk/KernelSU modules — `build_mode:
module` (or `both`) has this repo build one itself:

1. It ships a copy of the **stock** (unpatched) app's split APKs inside the
   module.
2. On install (or boot, if needed again), it force-installs those stock
   APKs via a real root `pm` session if the exact patched version isn't
   already installed — a session install can create, upgrade, *or
   downgrade* a package, so this works even with **no prior install of the
   app, or a different version installed**, not just an exact match.
3. It then bind-mounts the separately-patched APK directly over that
   now-genuinely-installed copy's `base.apk`. Android only checks an APK's
   signature at install time, not on every launch, so this file swap
   doesn't retrigger signature verification.
4. The mount is re-applied automatically at every boot, since bind mounts
   don't survive a reboot on their own.

Only `base.apk` and the relevant architecture's native-library split are
bundled for the stock install step — not every language/density split the
original upload contains — since none of that matters once the patched
`base.apk` is mounted over it anyway. This keeps module size and build time
down.

This is the same well-established technique used by tools like
[j-hc/revanced-magisk-module](https://github.com/j-hc/revanced-magisk-module);
this repo's implementation was written independently from scratch (that
project is GPL-3.0, and copying its code would carry that license's
obligations into this repo).

**Requires root** (Magisk or KernelSU). It's generated automatically, but
this repo's CI pipeline has no way to verify it actually mounts correctly
on a real device — please test it yourself before relying on it daily.
`build_mode: apk` (the default) is a normal, self-contained APK and is
unaffected by any of the above.

## Troubleshooting

**A release didn't get updated even though I expected a new version.**
Check the workflow run's logs — the builder tries every version its patches
currently support, newest first, and only gives up after all of them fail
or one is already released. The log shows exactly why each attempt was
skipped (e.g. no matching APKMirror release, no download variant, patch
failure, or "already released, nothing new to build").

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

**A Magisk/KernelSU module fails to flash with an architecture error.**
The module is built for one specific architecture (or "universal") — make
sure you're flashing the variant that matches your device.

**Telegram notifications aren't sending.**
They're optional. If `TG_TOKEN` isn't set as a repo secret, that step is
skipped automatically and never fails the build.

## Credits

- [Morphe](https://github.com/MorpheApp) — the patcher this project drives.
- [ReVanced](https://github.com/ReVanced) — the original patcher Morphe is
  based on.
- [crimera/piko](https://github.com/crimera/piko) — third-party
  Instagram/X patches used by the default config.
- [j-hc](https://github.com/j-hc) — this build pipeline's general structure,
  and the idea behind the Magisk module's stock-install-then-mount
  technique, were inspired by j-hc's ReVanced/Morphe builder templates.

## License

This project is licensed under the [MIT License](./LICENSE) — you're free
to use, modify, and redistribute it, including commercially, as long as the
original copyright notice is kept.

This choice is deliberate: none of this repo's own code is derived from
GPL-licensed sources (the Morphe CLI and patches are invoked as external
tools, not embedded, and the Magisk module scripts were written from
scratch rather than copied from GPL-3.0 projects), so nothing here requires
copyleft licensing.

## Disclaimer

This repository builds and distributes **modified versions of third-party
apps** using publicly available patches. It is not affiliated with, and the
patched apps are not endorsed by, the original app developers. Use at your
own risk and in accordance with the terms of service of the apps you patch.
