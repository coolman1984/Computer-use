"""The rules in .project-eye/rules.yaml, enforced rather than merely written.

A dependency rule nobody checks is a comment, and comments do not survive a
deadline. This runs the same scanner that draws the project graph and fails on
a forbidden import — so a change that quietly reaches across a boundary is
caught by the suite instead of by whoever inherits it.

It also holds the line that matters most: no import cycles. There are none
today, and a cycle is the one structural fault that nothing else in this
project's testing would notice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import project_eye  # noqa: E402  (the scanner lives beside the graph it writes)


@pytest.fixture(scope="module")
def modules():
    return project_eye.scan()


def test_no_module_imports_itself_in_a_circle(modules) -> None:
    """A cycle is the one structural fault nothing else here would notice."""
    found = project_eye.cycles(modules)

    assert not found, "import cycles: " + "; ".join(" -> ".join(cycle) for cycle in found)


def test_no_part_of_the_project_reaches_where_it_is_forbidden(modules) -> None:
    rules = project_eye.load_rules()
    assert rules, "the architecture rules are missing; .project-eye/rules.yaml is the contract"

    broken = project_eye.violations(modules, rules)

    assert not broken, "forbidden imports:\n" + "\n".join(
        f"  [{item['rule']}] {item['module']} imports {item['imports']}" for item in broken
    )


def test_the_written_graph_still_matches_the_code(modules) -> None:
    """A map that has drifted from the code is worse than none: people trust it."""
    import yaml

    path = ROOT / ".project-eye" / "graph.yaml"
    assert path.exists(), "run `python scripts/project_eye.py` to draw the graph"
    written = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert written["module_count"] == len(modules), (
        f"the graph describes {written['module_count']} modules and the code has "
        f"{len(modules)} — run `python scripts/project_eye.py`"
    )
    described = {node["id"] for node in written["nodes"]}
    assert described == set(modules), (
        "the graph and the code disagree about which modules exist — "
        "run `python scripts/project_eye.py`"
    )


def test_every_domain_a_module_lands_in_is_one_somebody_named(modules) -> None:
    """A module falling into a catch-all is a module whose owner nobody decided."""
    named = {domain for _, domain in project_eye.DOMAINS} | {
        "composition", "shared", "operations"
    }

    unnamed = sorted({described["domain"] for described in modules.values()} - named)

    assert not unnamed, f"modules landed in unnamed domains: {unnamed}"
