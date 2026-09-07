from pathlib import Path

from smartops.adapters.browser.session import open_browser_context
from smartops.config import BrowserSettings


def test_persistent_chrome_keeps_policy_extensions_enabled_without_side_loading(
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class Chromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            captured.update(user_data_dir=user_data_dir, **kwargs)
            return object()

    class Playwright:
        chromium = Chromium()

    settings = BrowserSettings(
        user_data_dir=str(tmp_path / "automation-profile"),
        profile_directory="Profile 19",
        enable_extensions=True,
        required_extension_ids=("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",),
    )

    open_browser_context(Playwright(), settings)

    assert not any(arg.startswith("--load-extension=") for arg in captured["args"])
    assert captured["ignore_default_args"] == ["--disable-extensions"]
