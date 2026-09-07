"""Four more shapes that defeat enterprise report portals, on top of the
seven already covered by `tests/test_torture_lab.py`.

None of these is exotic either: a grid whose rows arrive after the click that
requested them has returned, a sign-in that finishes inside a popup which then
closes itself, and two downloads whose real identity has nothing to do with
their name — a login page wearing ``.xlsx``, and a real workbook wearing
nothing at all. Every test here drives a real browser and, for the two
downloads, the platform's own validator on the resulting bytes, because the
failure these guard against is not a crash — it is a run that reports success
on the strength of a click, a filename, or an extension, none of which prove
anything.
"""

from __future__ import annotations

import base64
import io
import os
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import pytest

from smartops.adapters.validation.local import LocalFileValidator
from smartops.ports.validation import ValidationRules
from tests.recorded_site import LocalSite


def _chromium_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


@pytest.fixture
def site():
    with LocalSite() as running:
        yield running


@pytest.fixture
def recorded(services, site):
    """A system pointed at the torture pages, ready to record against."""
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter

    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    services.browser = PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )
    services.systems.save({
        "key": "portal",
        "name": "Torture portal",
        "auth": {"mode": "none"},
        "reports": [{"key": "daily", "title": "Daily", "url": f"{site.base_url}/torture/shadow.html"}],
    })
    return services


def _capture(services, url: str, script, *, seconds: float = 6.0):
    from tests.recording_harness import capture_with_recorder

    return capture_with_recorder(
        services, start_url=url, script=script,
        executable_path=_chromium_path(), seconds=seconds,
    )


def _actions(steps):
    return [step["action"] for step in steps]


# ---------- a results grid that fills after the click has already returned ----------


def test_a_late_filling_grid_is_proved_by_the_rows_not_by_the_click(recorded, site) -> None:
    """The click's own evidence must not claim the rows before they exist."""
    def search_then_open_the_row(page) -> None:
        page.click('#btnSearch')
        page.wait_for_selector('#btnRowDetail', state="visible")
        page.click('#btnRowDetail')
        page.wait_for_timeout(400)

    steps = _capture(recorded, f"{site.base_url}/torture/late_grid.html", search_then_open_the_row)

    clicks = [step for step in steps if step["action"] in {"click", "pointer_click"}]
    assert len(clicks) == 2, f"expected the search click and the row click: {_actions(steps)}"
    search_click, row_click = clicks

    # At the real moment the search was clicked — captured in the browser
    # itself, not by whenever the recorder got around to looking — the row
    # the search eventually produces did not exist yet. This is the fact that
    # makes the row a genuine new element rather than something that just
    # happened to already be on the page.
    seen_before_the_click = search_click["inputs"].get("_observed_visible_before") or []
    already_visible = " ".join(loc.get("value", "") for loc in seen_before_the_click)
    assert "resultRow0" not in already_visible and "btnRowDetail" not in already_visible, (
        "the row was already on the page before the search was even clicked"
    )

    # Compiling the recording is what turns the *later* action's arrival into
    # the search click's real proof — the rows appearing, not the click
    # landing. The row click's own locator already targets the freshly
    # appeared button, which on its own would say nothing about whether it
    # existed at the time of the search click; the plan compiler is what
    # connects the two.
    from smartops.recordings.converter import build_plan

    record = recorded.recordings.list(limit=1)[0]
    plan = build_plan(
        recording_id=record.id,
        system_key="portal",
        report_key="daily",
        steps=recorded.recordings.steps(record.id),
        start_url=f"{site.base_url}/torture/late_grid.html",
    )
    search_action = plan["actions"][0]
    assert search_action["success"]["type"] == "selector_visible", (
        f"the search click has no evidence at all: {search_action['success']}"
    )
    # The table is empty (zero size) until the rows land in it, so it only
    # becomes a *visible* element once they do — which is exactly why any of
    # these three count as the same proof: the grid, or something inside it,
    # showing up where nothing did before.
    proof = search_action["success"]["value"]
    assert any(name in proof for name in ("resultsGrid", "resultRow0", "btnRowDetail")), (
        f"the proof attached to the search click is not the grid that filled in: {search_action['success']}"
    )


# ---------- a sign-in that happens in a popup which then closes itself ----------


def _steps_matching(steps, *, action_in, page, locator_contains):
    matches = []
    for step in steps:
        if step["action"] not in action_in:
            continue
        if step["target"]["page"] != page:
            continue
        candidates = [step["locator"].get("value", ""), *step["locator"].get("fallbacks", [])]
        if any(locator_contains in candidate for candidate in candidates):
            matches.append(step)
    return matches


def test_a_self_closing_popup_sign_in_captures_what_happened_inside_it(recorded, site) -> None:
    """The popup's own steps must be recorded, in order, on the popup's own tab —
    and the tab the task started in must still be itself once the popup is gone.

    A first version of this test found the popup's typing and clicking simply
    missing, and initially concluded ``document``-level listeners do not fire
    at all in a `window.open()` popup in this headless Chromium build. The
    real cause, found on review, is narrower and worse: `context.add_init_script`
    runs `_CAPTURE_SCRIPT` exactly once for such a popup, against the transient
    `about:blank` document Chrome creates before the real page navigates in.
    The *window* survives that navigation; the document it closed over does
    not. Every listener bound to `document` was therefore bound to a document
    nobody could ever act in again, while a `window`-level listener — the
    outermost node in the capturing chain regardless of which document
    currently lives under it — kept receiving the popup's real events the
    whole time. `_CAPTURE_SCRIPT` now binds every listener to `window`
    instead, with a document read lazily (at event time) wherever one is
    still needed, and a same-window reinstall guard so a window that runs the
    script more than once — however that happens — cannot end up reporting
    every action twice.
    """
    def sign_in_through_a_popup(page) -> None:
        with page.context.expect_page() as popup_info:
            page.click('#btnSignIn')
        popup = popup_info.value
        popup.wait_for_load_state()
        popup.fill('#ssoUsername', 'shift-42')
        popup.click('#btnConfirmSignIn')
        page.wait_for_selector('#btnContinue:not([disabled])')
        page.click('#btnContinue')
        page.wait_for_timeout(500)

    steps = _capture(recorded, f"{site.base_url}/torture/popup_login.html", sign_in_through_a_popup)

    switch = next((step for step in steps if step["action"] == "switch_page"), None)
    assert switch is not None, f"the popup opening was never recorded: {_actions(steps)}"
    popup_name = switch["target"]["page"]
    assert popup_name.startswith("page-"), popup_name

    # A click on the main page, before the popup ever opened. If a window
    # could somehow end up running the capture script twice, this is where a
    # duplicate report would show up first — asserted here so a regression in
    # the reinstall guard fails loudly rather than only being "one extra
    # step" buried later in the list.
    signin_clicks = _steps_matching(
        steps, action_in={"click", "pointer_click"}, page="main", locator_contains="btnSignIn"
    )
    assert len(signin_clicks) == 1, f"the main page's own click was reported {len(signin_clicks)} times: {steps}"

    # The typing and the confirm click actually performed *inside* the popup —
    # the exact evidence the original bug made vanish — attributed to the
    # popup's own tab identity, each exactly once.
    typed = _steps_matching(steps, action_in={"fill"}, page=popup_name, locator_contains="ssoUsername")
    assert len(typed) == 1, f"typing inside the popup was lost or duplicated: {_actions(steps)}"
    assert typed[0]["inputs"]["value"] == "shift-42"

    confirmed = _steps_matching(
        steps, action_in={"click", "pointer_click"}, page=popup_name, locator_contains="btnConfirmSignIn"
    )
    assert len(confirmed) == 1, f"the confirm click inside the popup was lost or duplicated: {_actions(steps)}"

    # In order: the popup is tracked, then it is typed into, then confirmed.
    assert switch["seq"] < typed[0]["seq"] < confirmed[0]["seq"]

    # The step after the popup closes itself must still run against the tab
    # the task actually started in — not "latest", and not the tab that just
    # vanished — exactly once.
    continued = _steps_matching(
        steps, action_in={"click", "pointer_click"}, page="main", locator_contains="btnContinue"
    )
    assert len(continued) == 1, f"the step after the popup closed was lost or duplicated: {_actions(steps)}"
    assert confirmed[0]["seq"] < continued[0]["seq"]


# ---------- a download that is really a login page named report.xlsx ----------


def test_a_login_page_saved_as_report_xlsx_fails_validation(recorded, site) -> None:
    """The extension says workbook; the bytes say sign-in page. The bytes must win."""
    def download_the_fake_workbook(page) -> None:
        page.click('#btnDownloadFakeWorkbook')
        page.wait_for_timeout(500)

    steps = _capture(
        recorded, f"{site.base_url}/torture/fake_workbook_login.html", download_the_fake_workbook
    )

    downloads = [step for step in steps if step["action"] == "download"]
    assert downloads, f"the download never happened: {_actions(steps)}"
    assert downloads[0]["inputs"]["file_name"] == "report.xlsx"

    record = recorded.recordings.list(limit=1)[0]
    saved = Path(record.artifact_dir) / downloads[0]["download_ref"]
    assert saved.exists() and saved.stat().st_size > 0

    report = LocalFileValidator().validate(saved, ValidationRules(expected_extensions=(".xlsx",)))

    assert not report.passed, "an HTML sign-in page named report.xlsx must not validate as a workbook"
    assert any(
        "web page" in failure or "is not one" in failure for failure in report.failures
    ), report.failures


# ---------- a download with no extension whose bytes are a real workbook ----------

# A hand-built minimal OOXML package: just enough parts for _is_xlsx_workbook
# (and the row reader) to recognise it, with no dependency beyond zipfile.
_XLSX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_XLSX_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="xl/workbook.xml"/>
</Relationships>"""

_XLSX_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>"""

_XLSX_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
    Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _minimal_xlsx_bytes(rows: list[list[str]]) -> bytes:
    """A real, structurally valid .xlsx workbook, built in memory with zipfile."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    row_xml_parts = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row):
            col_letter = chr(ord("A") + c_idx)
            ref = f"{col_letter}{r_idx}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        row_xml_parts.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_xml_parts)}</sheetData></worksheet>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", _XLSX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _XLSX_ROOT_RELS)
        archive.writestr("xl/workbook.xml", _XLSX_WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _XLSX_WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def _action(seq: int, action: str, **kwargs):
    step = {
        "seq": seq,
        "action": action,
        "target": {"page": "main", "frame": ""},
        "locator": {},
        "inputs": {},
        "success": {"type": "none"},
        "retry": {"max_attempts": 1, "safe_to_repeat": False},
    }
    step.update(kwargs)
    return step


@pytest.fixture
def engine(services):
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter

    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    return PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )


def test_an_extensionless_download_that_is_really_a_workbook_is_identified_and_accepted(
    services, engine, site
) -> None:
    """Only the platform's existing replay path renames it; this proves that path does."""
    from smartops.ports.browser import ReplayRequest
    from smartops.sessions import session_path

    payload = base64.b64encode(
        _minimal_xlsx_bytes([["reference", "amount"], ["A-1", "10"]])
    ).decode("ascii")
    destination = Path(services.settings.storage.raw_data_dir) / "replay-advanced"
    destination.mkdir(parents=True, exist_ok=True)

    result = engine.replay(ReplayRequest(
        system="portal",
        report="daily",
        destination_dir=destination,
        plan={
            "plan_version": 2,
            "start_url": f"{site.base_url}/torture/xlsx_no_extension.html?payload={quote(payload, safe='')}",
            "actions": [_action(1, "click", locator={"strategy": "css", "value": '[id="btnDownload"]'})],
            "expects_download": True,
            "expected_download_count": 1,
        },
        session_state_path=session_path(services.settings.storage.sessions_dir, "portal"),
    ))

    assert result.ok, result.message
    saved = Path(result.file_path)
    assert saved.exists()
    # The download itself had no extension at all; only the platform's own
    # inspection of its bytes could have put ".xlsx" on the end.
    assert saved.suffix == ".xlsx", f"the real workbook was not identified from its bytes: {saved.name}"

    report = LocalFileValidator().validate(saved, ValidationRules(expected_extensions=(".xlsx",)))
    assert report.passed, report.failures
    assert report.row_count == 1
