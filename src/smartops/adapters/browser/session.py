"""Open Chrome consistently for checks, recording, replay, and extraction.

Ordinary sites use Playwright's isolated context.  Corporate portals may need
policy-installed extensions, so they can opt into a dedicated persistent Chrome
profile outside the repository.  Keeping that decision here prevents recording
and unattended replay from silently using different browser environments.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import BrowserSettings
from ...core.errors import ConfigurationError


@dataclass
class BrowserContextSession:
    """The context plus whichever owning object must be closed."""

    context: Any
    browser: Any | None = None

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            if self.browser is not None:
                self.browser.close()


def _resolve_extension_dir(configured: str) -> Path:
    """Resolve a version folder, or the newest version below a stable ID root."""
    root = Path(configured).expanduser()
    if (root / "manifest.json").is_file():
        return root.resolve()
    if root.is_dir():
        candidates = [
            child
            for child in root.iterdir()
            if child.is_dir() and (child / "manifest.json").is_file()
        ]
        if candidates:
            return max(
                candidates,
                key=lambda child: (child.stat().st_mtime_ns, child.name),
            ).resolve()
    raise ConfigurationError(f"Configured Chrome extension was not found: {root}")


def open_browser_context(
    playwright: Any,
    settings: BrowserSettings,
    *,
    headless: bool | None = None,
    executable_path: str | None = None,
    accept_downloads: bool = False,
    storage_state_path: Path | str | None = None,
) -> BrowserContextSession:
    """Open one Chrome context using the configured isolation model."""
    chosen_headless = settings.headless if headless is None else headless
    chosen_executable = executable_path or settings.executable_path or ""
    common: dict[str, Any] = {"headless": chosen_headless}
    if chosen_executable:
        common["executable_path"] = chosen_executable
    else:
        common["channel"] = "chrome"

    viewport = {
        "width": settings.viewport_width,
        "height": settings.viewport_height,
    }
    configured_dir = settings.user_data_dir.strip()
    if configured_dir:
        profile_root = Path(configured_dir).expanduser()
        if not profile_root.is_absolute():
            raise ConfigurationError(
                "browser.user_data_dir must be an absolute path outside the repository"
            )
        profile_root.mkdir(parents=True, exist_ok=True)
        persistent: dict[str, Any] = {
            **common,
            "accept_downloads": accept_downloads,
            "viewport": viewport,
        }
        args: list[str] = []
        if settings.profile_directory.strip():
            args.append(f"--profile-directory={settings.profile_directory.strip()}")
        if settings.enable_extensions and settings.extension_paths:
            extension_dirs = [
                str(_resolve_extension_dir(path)) for path in settings.extension_paths
            ]
            args.append(f"--load-extension={','.join(extension_dirs)}")
        if args:
            persistent["args"] = args
        if settings.enable_extensions:
            # Keep all other Playwright defaults; only remove the switch that
            # suppresses Chrome's enterprise-managed extensions.
            persistent["ignore_default_args"] = ["--disable-extensions"]
        context = playwright.chromium.launch_persistent_context(
            str(profile_root), **persistent
        )
        return BrowserContextSession(context=context)

    browser = playwright.chromium.launch(**common)
    context_options: dict[str, Any] = {
        "accept_downloads": accept_downloads,
        "viewport": viewport,
    }
    if storage_state_path and Path(storage_state_path).exists():
        context_options["storage_state"] = str(storage_state_path)
    context = browser.new_context(**context_options)
    return BrowserContextSession(context=context, browser=browser)
