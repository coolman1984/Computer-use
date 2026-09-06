from pathlib import Path

from smartops.adapters.browser.session import open_browser_context
from smartops.config import BrowserSettings


def test_persistent_chrome_loads_a_required_extension_from_its_stable_root(
    tmp_path: Path,
) -> None:
    extension_root = tmp_path / "extensions" / "example-extension-id"
    installed_version = extension_root / "1.2.3_0"
    installed_version.mkdir(parents=True)
    (installed_version / "manifest.json").write_text("{}", encoding="utf-8")
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
        extension_paths=(str(extension_root),),
    )

    open_browser_context(Playwright(), settings)

    assert f"--load-extension={installed_version}" in captured["args"]
    assert captured["ignore_default_args"] == ["--disable-extensions"]
