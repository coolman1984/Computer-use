"""The server may suggest a filename, but never chooses SmartOps' path."""
from __future__ import annotations

from smartops.adapters.browser.downloads import (
    discard_empty_reservation,
    reserve_download_path,
    safe_download_name,
)


def test_suggested_filename_cannot_escape_the_run_directory(tmp_path) -> None:
    name = safe_download_name(r"..\\..\\CON:report?.xlsx")
    assert "/" not in name and "\\" not in name and ":" not in name and "?" not in name

    target = reserve_download_path(tmp_path, r"../../outside.xlsx")
    assert target.parent == tmp_path
    assert target.name == "outside.xlsx"


def test_reserved_names_and_duplicate_suggestions_are_safe_and_unique(tmp_path) -> None:
    reserved = reserve_download_path(tmp_path, "NUL.xlsx")
    first = reserve_download_path(tmp_path, "daily report.csv")
    second = reserve_download_path(tmp_path, "daily report.csv")

    assert reserved.name == "_NUL.xlsx"
    assert first.name == "daily report.csv"
    assert second.name == "daily report-2.csv"
    assert all(path.exists() and path.stat().st_size == 0 for path in (reserved, first, second))


def test_empty_reservation_is_removed_but_download_data_is_preserved(tmp_path) -> None:
    empty = reserve_download_path(tmp_path, "broken.csv")
    complete = reserve_download_path(tmp_path, "partial.csv")
    complete.write_bytes(b"diagnostic bytes")

    discard_empty_reservation(empty)
    discard_empty_reservation(complete)

    assert not empty.exists()
    assert complete.read_bytes() == b"diagnostic bytes"
