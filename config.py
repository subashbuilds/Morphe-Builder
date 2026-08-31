"""Loads and validates config.yml into typed, easy-to-use dataclasses.

Keeping all of the "what does this YAML key mean / what's the default"
knowledge in one place means main.py and friends never touch raw dicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_ARCHITECTURES = {"arm64-v8a", "armeabi-v7a", "x86_64", "x86", "universal"}
VALID_BUILD_MODES = {"apk", "module", "both"}
VALID_CHANNELS = {"latest", "dev"}


class ConfigError(Exception):
    """Raised when config.yml is missing a required field or has a bad value."""


@dataclass
class SourceConfig:
    """Where to fetch a GitHub-released asset (the CLI jar or a patches file) from."""

    repo: str
    asset_regex: str
    channel: str = "latest"  # "latest" or "dev"

    @property
    def include_prereleases(self) -> bool:
        return self.channel == "dev"


@dataclass
class ModuleConfig:
    id: str
    mount_path: str = "auto"


@dataclass
class AppConfig:
    id: str
    name: str
    package_name: str
    apkmirror_org: str
    apkmirror_app: str
    apkmirror_release_prefix: str
    patches: SourceConfig
    cli: SourceConfig
    enabled: bool = True
    build_mode: str = "apk"
    architectures: list[str] = field(default_factory=lambda: ["universal"])
    include_patches: list[str] = field(default_factory=list)
    exclude_patches: list[str] = field(default_factory=list)
    module: ModuleConfig | None = None
    pinned_version: str | None = None

    @property
    def wants_apk(self) -> bool:
        return self.build_mode in ("apk", "both")

    @property
    def wants_module(self) -> bool:
        return self.build_mode in ("module", "both")

    def apkmirror_release_url(self, version_dashed_or_dotted: str) -> str:
        version_dashed = version_dashed_or_dotted.replace(".", "-")
        return (
            f"https://www.apkmirror.com/apk/{self.apkmirror_org}/{self.apkmirror_app}/"
            f"{self.apkmirror_release_prefix}-{version_dashed}-release/"
        )

    def apkmirror_listing_url(self) -> str:
        return f"https://www.apkmirror.com/apk/{self.apkmirror_org}/{self.apkmirror_app}/"


def _require(d: dict, key: str, ctx: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{ctx}: missing required field '{key}'")
    return d[key]


def _validate_choice(value: str, valid: set[str], ctx: str) -> str:
    if value not in valid:
        raise ConfigError(f"{ctx}: '{value}' is not one of {sorted(valid)}")
    return value


def _validate_regex(pattern: str, ctx: str) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ConfigError(f"{ctx}: invalid regex '{pattern}': {e}")
    return pattern


def _build_source_config(raw: dict | None, base_defaults: dict, ctx: str) -> SourceConfig:
    raw = raw or {}
    merged = {**base_defaults, **raw}
    repo = _require(merged, "repo", ctx)
    if "/" not in repo:
        raise ConfigError(f"{ctx}: repo '{repo}' should look like 'owner/name'")
    asset_regex = _validate_regex(_require(merged, "asset_regex", ctx), ctx)
    channel = _validate_choice(str(merged.get("channel", "latest")), VALID_CHANNELS, ctx)
    return SourceConfig(repo=repo, asset_regex=asset_regex, channel=channel)


def load_config(path: str | Path = "config.yml") -> list[AppConfig]:
    """Parse config.yml and return the list of configured apps (enabled or not)."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    defaults = raw.get("defaults", {}) or {}
    default_cli = defaults.get("cli", {}) or {}
    default_patches = defaults.get("patches", {}) or {}
    default_architectures = defaults.get("architectures", ["universal"])
    default_build_mode = defaults.get("build_mode", "apk")

    apps_raw = raw.get("apps")
    if not apps_raw:
        raise ConfigError("config.yml has no 'apps' list")

    apps: list[AppConfig] = []
    seen_ids: set[str] = set()

    for i, app_raw in enumerate(apps_raw):
        ctx = f"apps[{i}]"
        app_id = str(_require(app_raw, "id", ctx))
        ctx = f"apps.{app_id}"

        if not re.fullmatch(r"[a-z0-9_\-]+", app_id):
            raise ConfigError(f"{ctx}: id must be lowercase letters/numbers/_/- only")
        if app_id in seen_ids:
            raise ConfigError(f"{ctx}: duplicate app id")
        seen_ids.add(app_id)

        name = str(_require(app_raw, "name", ctx))
        package_name = str(_require(app_raw, "package_name", ctx))

        apkmirror_raw = _require(app_raw, "apkmirror", ctx)
        apkmirror_org = str(_require(apkmirror_raw, "org", f"{ctx}.apkmirror"))
        apkmirror_app = str(_require(apkmirror_raw, "app", f"{ctx}.apkmirror"))
        # Usually identical to `app` (true for YouTube/YouTube Music), but a
        # few apps (Instagram is one) use a shorter/different prefix in
        # their release-page URLs than in their listing directory slug.
        apkmirror_release_prefix = str(apkmirror_raw.get("release_prefix", apkmirror_app))

        cli_cfg = _build_source_config(app_raw.get("cli"), default_cli, f"{ctx}.cli")
        patches_cfg = _build_source_config(
            app_raw.get("patches"), default_patches, f"{ctx}.patches"
        )

        build_mode = _validate_choice(
            str(app_raw.get("build_mode", default_build_mode)), VALID_BUILD_MODES, ctx
        )

        architectures = app_raw.get("architectures", default_architectures)
        if not architectures:
            raise ConfigError(f"{ctx}: architectures must not be empty")
        for arch in architectures:
            _validate_choice(arch, VALID_ARCHITECTURES, f"{ctx}.architectures")

        module_cfg = None
        module_raw = app_raw.get("module")
        if build_mode in ("module", "both"):
            if not module_raw or "id" not in module_raw:
                raise ConfigError(
                    f"{ctx}: build_mode '{build_mode}' requires a 'module.id' to be set"
                )
            module_cfg = ModuleConfig(
                id=str(module_raw["id"]),
                mount_path=str(module_raw.get("mount_path", "auto")),
            )

        apps.append(
            AppConfig(
                id=app_id,
                name=name,
                package_name=package_name,
                apkmirror_org=apkmirror_org,
                apkmirror_app=apkmirror_app,
                apkmirror_release_prefix=apkmirror_release_prefix,
                patches=patches_cfg,
                cli=cli_cfg,
                enabled=bool(app_raw.get("enabled", True)),
                build_mode=build_mode,
                architectures=list(architectures),
                include_patches=list(app_raw.get("include_patches", []) or []),
                exclude_patches=list(app_raw.get("exclude_patches", []) or []),
                module=module_cfg,
                pinned_version=(
                    str(app_raw["version"]) if app_raw.get("version") else None
                ),
            )
        )

    return apps


def enabled_apps(path: str | Path = "config.yml") -> list[AppConfig]:
    return [a for a in load_config(path) if a.enabled]
