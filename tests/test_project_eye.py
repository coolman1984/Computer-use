"""Keep the project map honest as cross-domain code evolves."""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_RELATIONS = {
    "contains", "owns", "imports", "calls", "invokes", "renders", "dispatches",
    "publishes", "subscribes", "reads", "writes", "implements", "depends_on",
    "routes_to", "configured_by", "tested_by", "observed_by",
}


def _yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_project_eye_graph_points_to_real_owned_code() -> None:
    graph = _yaml(".project-eye/graph.yaml")
    nodes = graph["nodes"]
    node_ids = [node["id"] for node in nodes]

    assert len(node_ids) == len(set(node_ids))
    for node in nodes:
        assert (ROOT / node["path"]).exists(), node["id"]

    for edge in graph["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids
        assert edge["relation"] in ALLOWED_RELATIONS


def test_project_eye_rules_have_explicit_boundaries() -> None:
    rules = _yaml(".project-eye/rules.yaml")["rules"]
    assert rules
    for rule in rules:
        assert rule["id"].startswith("EYE-")
        assert rule["from"].startswith("src/smartops/")
        assert rule["reason"]
