"""إدارة جلسات الدخول المحفوظة (storage_state) لكل نظام.

المبدأ: المنصة لا ترى كلمة مرور أبدًا. الإنسان يسجّل الدخول مرة واحدة في
متصفح مرئي (headed)، ثم نحفظ حالة الجلسة (كوكيز + توكنز) في ملف خارج
المستودع، وبعدها المتصفح التلقائي يعيد استخدامها بدل الدخول من جديد
(D020). جلسة منتهية أثناء التشغيل تُكتشف وتُرفع كـ AuthError (راجع
adapters/browser/playwright_engine.py و workflows/builtin.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .config import BrowserSettings
from .core.clock import Clock, SystemClock
from .core.errors import ConfigurationError
from .storage.paths import slug


def session_path(sessions_dir: Path | str, system_key: str) -> Path:
    """مسار ملف الجلسة لنظام بعينه: <sessions_dir>/<slug(system_key)>.json"""
    return Path(sessions_dir) / f"{slug(system_key)}.json"


def session_exists(sessions_dir: Path | str, system_key: str) -> bool:
    return session_path(sessions_dir, system_key).exists()


def session_age_hours(
    sessions_dir: Path | str, system_key: str, *, now: Clock | None = None
) -> float | None:
    """عمر الجلسة بالساعات، أو None لو الملف غير موجود."""
    path = session_path(sessions_dir, system_key)
    if not path.exists():
        return None
    clock = now or SystemClock()
    modified = path.stat().st_mtime
    current = clock.now().timestamp()
    return max(0.0, (current - modified) / 3600.0)


def capture_login(
    system_key: str,
    login_url: str,
    *,
    sessions_dir: Path | str,
    browser_settings: BrowserSettings,
    logged_in_selector: str = "",
    executable_path: str | None = None,
    wait_for_enter: Callable[[], None] | None = None,
    timeout_seconds: float = 600.0,
) -> Path:
    """يفتح متصفحًا مرئيًا لتسجيل دخول يدوي، ثم يحفظ storage_state.

    لا يُدخل أي كلمة مرور برمجيًا؛ الإنسان هو من يسجّل الدخول في النافذة
    المفتوحة. لو فيه جلسة سابقة (حتى جزئية الصلاحية) نحمّلها أولًا حتى لا
    يضطر المستخدم لتسجيل دخول كامل من الصفر في كل مرة.
    """
    if not login_url:
        raise ConfigurationError(
            f"لا يوجد login_url لتسجيل الدخول للنظام {system_key}",
            details={"system": system_key},
        )

    from playwright.sync_api import sync_playwright  # استيراد مؤجل: لا حاجة له في كل الوحدات

    target_path = session_path(sessions_dir, system_key)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs: dict = {"headless": False}
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            context_kwargs: dict = {
                "viewport": {
                    "width": browser_settings.viewport_width,
                    "height": browser_settings.viewport_height,
                }
            }
            if target_path.exists():
                context_kwargs["storage_state"] = str(target_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(login_url)

            print(  # noqa: T201 — رسالة تفاعلية مقصودة للمشغّل، مش سجل أحداث
                f"سجّل الدخول يدويًا للنظام {system_key} في النافذة المفتوحة، "
                "ثم ارجع هنا واضغط Enter لما تخلص."
            )
            if logged_in_selector:
                page.wait_for_selector(logged_in_selector, timeout=timeout_seconds * 1000)
            else:
                (wait_for_enter or (lambda: input()))()

            context.storage_state(path=str(target_path))
        finally:
            browser.close()

    try:
        os.chmod(target_path, 0o600)
    except OSError:
        pass  # ويندوز أو نظام ملفات لا يدعم صلاحيات POSIX

    return target_path
