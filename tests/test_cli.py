"""F-10 tests: the command line interface — argument parsing, plus
doctor/systems/login running for real against Services fully isolated inside
tmp_path (via SMARTOPS_CONFIG). No real browser is ever launched here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smartops.cli import build_parser, main


def test_parser_accepts_all_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["systems"]).command == "systems"

    login_args = parser.parse_args(["login", "erp_demo"])
    assert login_args.command == "login"
    assert login_args.system == "erp_demo"

    collect_args = parser.parse_args(["collect", "erp_demo", "daily_sales"])
    assert collect_args.command == "collect"
    assert collect_args.system == "erp_demo"
    assert collect_args.report == "daily_sales"

    assert parser.parse_args(["work"]).command == "work"
    assert parser.parse_args(["serve"]).command == "serve"


def test_help_prints_with_legacy_windows_encoding(monkeypatch) -> None:
    class LegacyStream:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.parts: list[str] = []

        def write(self, value: str) -> int:
            value.encode(self.encoding, errors="strict")
            self.parts.append(value)
            return len(value)

        def flush(self) -> None:
            pass

        def reconfigure(self, *, encoding: str, errors: str) -> None:
            self.encoding = encoding

    stream = LegacyStream()
    monkeypatch.setattr("sys.stdout", stream)

    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    assert "recordings-backup" in "".join(stream.parts)


def test_parser_rejects_unknown_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["not-a-real-command"])


def test_parser_requires_a_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


SYSTEM_YAML_SESSION = """
key: erp_demo
name: Demo system
auth:
  mode: session
  login_url: "https://intranet.example.local/login"
reports:
  - key: daily_sales
    title: Daily sales report
    url: "https://intranet.example.local/reports/daily-sales"
    download_selector: "#dl"
"""

SYSTEM_YAML_NO_AUTH = """
key: no_auth_system
name: System without authentication
reports:
  - key: public_report
    title: Public report
    url: "https://intranet.example.local/reports/public"
    download_selector: "#dl"
"""


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch) -> Path:
    """Build fully isolated settings inside tmp_path via SMARTOPS_CONFIG,
    instead of depending on the real project folder or the user's settings."""
    systems_dir = tmp_path / "systems"
    systems_dir.mkdir()
    (systems_dir / "erp.yaml").write_text(SYSTEM_YAML_SESSION, encoding="utf-8")
    (systems_dir / "no_auth.yaml").write_text(SYSTEM_YAML_NO_AUTH, encoding="utf-8")

    config_path = tmp_path / "system.yaml"
    config_path.write_text(
        "storage:\n"
        f"  sqlite_path: {tmp_path / 'db' / 'smartops.db'}\n"
        f"  raw_data_dir: {tmp_path / 'raw'}\n"
        f"  incidents_dir: {tmp_path / 'incidents'}\n"
        f"  logs_dir: {tmp_path / 'logs'}\n"
        f"  history_dir: {tmp_path / 'history'}\n"
        f"  sessions_dir: {tmp_path / 'sessions'}\n"
        f"  systems_dir: {systems_dir}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTOPS_CONFIG", str(config_path))
    return tmp_path


def test_doctor_command_runs_and_prints_report(isolated_env: Path, capsys) -> None:
    exit_code = main(["doctor"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sessions_dir" in output
    assert "erp_demo" in output
    assert "no saved session" in output


def test_systems_command_lists_systems_and_reports(isolated_env: Path, capsys) -> None:
    exit_code = main(["systems"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "erp_demo" in output
    assert "daily_sales" in output
    assert "no_auth_system" in output


def test_login_on_no_auth_system_fails_clearly(isolated_env: Path, capsys) -> None:
    exit_code = main(["login", "no_auth_system"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "none" in output


def test_login_on_unknown_system_reports_configuration_error(isolated_env: Path, capsys) -> None:
    exit_code = main(["login", "does_not_exist"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Error" in output
