"""Capture the visible Chrome window on the interactive Windows desktop.

This is deliberately a read-only diagnostic helper for reviewing a human-led
portal recording.  It does not send clicks or keys, and it does not inspect the
page or workbook content.  The image is written as a BMP so no extra package is
needed on the Windows automation machine.
"""

from __future__ import annotations

import argparse
import ctypes
from pathlib import Path

import win32con
import win32gui
import win32service


def _attach_to_interactive_desktop() -> object:
    desktop = win32service.OpenDesktop("default", 0, False, win32con.GENERIC_ALL)
    if not ctypes.windll.user32.SetThreadDesktop(int(desktop)):
        raise OSError("Could not attach to the interactive Windows desktop")
    return desktop


def _foreground_chrome() -> int:
    foreground = win32gui.GetForegroundWindow()
    if foreground and win32gui.GetClassName(foreground) == "Chrome_WidgetWin_1":
        return foreground

    found: list[int] = []

    def visit(window: int, _unused: object) -> bool:
        if win32gui.IsWindowVisible(window) and win32gui.GetClassName(window) == "Chrome_WidgetWin_1":
            found.append(window)
        return True

    win32gui.EnumWindows(visit, None)
    if not found:
        raise RuntimeError("No visible Google Chrome window was found on the interactive desktop")
    return found[0]


def capture(output: Path) -> Path:
    _attach_to_interactive_desktop()
    # ``win32ui`` can initialise UI state for the current desktop.  Loading it
    # only after SetThreadDesktop keeps this helper attached to WinSta0\\Default.
    import win32ui

    window = _foreground_chrome()
    left, top, right, bottom = win32gui.GetWindowRect(window)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("The Chrome window has no visible area")

    # A normal desktop capture sees Chromium's composited GPU surface, unlike
    # copying the window DC.  Pillow is available on this Windows machine; keep
    # the native path below as a dependency-free fallback for other machines.
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            image.save(output)
        finally:
            image.close()
        return output
    except ImportError:
        pass

    source_dc = win32gui.GetWindowDC(window)
    source = win32ui.CreateDCFromHandle(source_dc)
    target = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    target.SelectObject(bitmap)
    try:
        # Chromium's GPU surface can be black when copied from its window DC.
        # Ask Windows to render the window's full content first; this stays
        # read-only and works when the window is partially occluded.
        rendered = bool(ctypes.windll.user32.PrintWindow(window, target.GetSafeHdc(), 2))
        if not rendered or not any(bitmap.GetBitmapBits(True)):
            target.BitBlt((0, 0), (width, height), source, (0, 0), win32con.SRCCOPY)
        output.parent.mkdir(parents=True, exist_ok=True)
        bitmap.SaveBitmapFile(target, str(output))
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(window, source_dc)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Destination .bmp file")
    args = parser.parse_args()
    print(capture(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
