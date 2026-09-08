"""Derive the project's dependency graph from the code, not from memory.

A hand-drawn architecture diagram is out of date the week after it is drawn,
and the version that is wrong is worse than none: people trust it. This reads
the imports that actually exist, groups modules into the domains the project
actually has, and writes the graph out. Run it after a change and the map moves
with the code.

It answers three questions the AGENTS.md contract requires to be kept apart:
what the architecture is *meant* to be (`.project-eye/rules.yaml`, written by
hand because intent cannot be derived), what the code *actually* imports (this
scanner), and where the two disagree (the drift report, and the guardian test
that fails on a forbidden edge).

    python scripts/project_eye.py            # rewrite .project-eye/graph.yaml
    python scripts/project_eye.py --check    # report drift without writing
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "smartops"
GRAPH_PATH = ROOT / ".project-eye" / "graph.yaml"
RULES_PATH = ROOT / ".project-eye" / "rules.yaml"

# Which domain a module belongs to, by responsibility rather than by file type.
# Order matters: the first match wins, so a more specific prefix comes first.
DOMAINS: tuple[tuple[str, str], ...] = (
    ("recordings/", "recording"),
    ("adapters/browser/", "browser"),
    ("adapters/validation/", "validation"),
    ("adapters/history/", "results"),
    ("adapters/incidents/", "results"),
    ("adapters/notify/", "results"),
    ("adapters/agents/", "assistant"),
    ("processes/", "automation"),
    ("workflows/", "automation"),
    ("engine/", "automation"),
    ("api/", "web"),
    ("storage/", "persistence"),
    ("domain/", "contract"),
    ("ports/", "contract"),
    ("core/", "foundation"),
    ("events/", "foundation"),
)

# Modules that are not a domain at all. The composition root wires every domain
# together and is allowed to depend on all of them; calling that a dependency
# cycle would be reading the wiring as if it were a domain. Kept separate so the
# graph says which two-way edges are real and which are the wiring doing its job.
COMPOSITION = {"services", "cli", "main", "__main__", "worker", "scheduler", "guidance"}

# Shared settings and small helpers every domain may read. Not "core-flow": a
# catch-all named after nothing is how a project stops being able to answer
# "who owns this?".
SHARED = {"config", "sessions", "credentials", "credential_prompt", "storage/paths"}

# Modules that are the front door to a running program rather than a library.
ENTRYPOINTS = {
    "cli": "the operator's terminal commands, and the one that serves the app",
    "main": "the web application, as a server would import it",
    "__main__": "python -m smartops",
    "worker": "the background worker that executes runs",
    "scheduler": "the clock that starts scheduled runs",
    "credential_prompt": "the separate window that asks for a password",
    "services": "the object graph everything else is reached through",
}


def module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE).with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return "/".join(parts) or "__init__"


def domain_of(name: str) -> str:
    if name in COMPOSITION:
        return "composition"
    if name in SHARED:
        return "shared"
    for prefix, domain in DOMAINS:
        if name.startswith(prefix):
            return domain
    return "operations"


def imports_of(path: Path, name: str) -> set[str]:
    """Every other module inside this package that this file imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    here = name.split("/")[:-1]
    found: set[str] = set()

    def record(target: str) -> None:
        target = target.strip("/")
        if not target:
            return
        # A package import resolves to that package's __init__; keep the
        # package itself as the node so the graph stays readable.
        if (PACKAGE / f"{target}.py").exists() or (PACKAGE / target).is_dir():
            found.add(target)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import: resolve it against this module
                base = here[: len(here) - (node.level - 1)] if node.level > 1 else here
                record("/".join([*base, *(node.module or "").split(".")]))
            elif (node.module or "").startswith("smartops"):
                record("/".join((node.module or "").split(".")[1:]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("smartops."):
                    record("/".join(alias.name.split(".")[1:]))
    return {item for item in found if item != name}


def scan() -> dict:
    modules: dict[str, dict] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = module_name(path)
        modules[name] = {
            "domain": domain_of(name),
            "path": str(path.relative_to(ROOT)),
            "lines": len(path.read_text(encoding="utf-8").splitlines()),
            "imports": sorted(imports_of(path, name)),
        }
    for name, described in modules.items():
        described["imports"] = [item for item in described["imports"] if item in modules]
    return modules


def cycles(modules: dict[str, dict]) -> list[list[str]]:
    """Import cycles, which are the one structural fault nothing else catches."""
    found: list[list[str]] = []
    seen: set[str] = set()

    def walk(name: str, trail: list[str], on_path: set[str]) -> None:
        for target in modules[name]["imports"]:
            if target in on_path:
                cycle = trail[trail.index(target):] + [target]
                signature = tuple(sorted(set(cycle)))
                if signature not in seen:
                    seen.add(signature)
                    found.append(cycle)
                continue
            if len(trail) > 12:
                return
            walk(target, trail + [target], on_path | {target})

    for name in modules:
        walk(name, [name], {name})
    return found


def domain_edges(modules: dict[str, dict]) -> dict[tuple[str, str], list[str]]:
    edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, described in modules.items():
        for target in described["imports"]:
            source, sink = described["domain"], modules[target]["domain"]
            if source != sink:
                edges[(source, sink)].append(f"{name} -> {target}")
    return edges


def two_way_domains(modules: dict[str, dict]) -> list[tuple[str, str]]:
    """Domains that each depend on the other.

    Not an import cycle — those are counted separately and there are none — but
    the thing that becomes one. Two domains that reach into each other cannot be
    reasoned about, tested, or replaced apart, and the composition root is
    excluded because wiring everything together is its whole job.
    """
    pairs = {
        (described["domain"], modules[target]["domain"])
        for described in modules.values()
        for target in described["imports"]
        if described["domain"] != modules[target]["domain"]
    }
    both = {
        tuple(sorted(pair)) for pair in pairs
        if (pair[1], pair[0]) in pairs and "composition" not in pair
    }
    return sorted(both)


def violations(modules: dict[str, dict], rules: list[dict]) -> list[dict]:
    """Every import the architecture rules forbid, with the file that made it."""
    broken: list[dict] = []
    for name, described in modules.items():
        source = described["domain"]
        for rule in rules:
            if rule.get("from") != source:
                continue
            for target in described["imports"]:
                if modules[target]["domain"] in (rule.get("forbidden_to") or []):
                    broken.append({
                        "rule": rule.get("id", "?"),
                        "module": name,
                        "imports": target,
                        "why": rule.get("why", ""),
                    })
    return broken


def load_rules() -> list[dict]:
    if not RULES_PATH.exists():
        return []
    import yaml

    return (yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")) or {}).get("rules") or []


def render(modules: dict[str, dict]) -> str:
    import yaml

    by_domain: dict[str, list[str]] = defaultdict(list)
    for name, described in modules.items():
        by_domain[described["domain"]].append(name)

    fan_in: dict[str, int] = defaultdict(int)
    for described in modules.values():
        for target in described["imports"]:
            fan_in[target] += 1

    document = {
        "generated_by": "scripts/project_eye.py — do not edit by hand, run it again",
        "module_count": len(modules),
        "domains": {
            domain: {
                "modules": sorted(names),
                "lines": sum(modules[name]["lines"] for name in names),
            }
            for domain, names in sorted(by_domain.items())
        },
        "nodes": [
            {
                "id": name,
                "type": "module",
                "domain": described["domain"],
                "path": described["path"],
                "lines": described["lines"],
                "depended_on_by": fan_in.get(name, 0),
            }
            for name, described in sorted(modules.items())
        ],
        "edges": [
            {"from": name, "to": target, "relation": "imports"}
            for name, described in sorted(modules.items())
            for target in described["imports"]
        ],
        "domain_edges": [
            {"from": source, "to": sink, "relation": "depends_on", "through": sorted(examples)[:3]}
            for (source, sink), examples in sorted(domain_edges(modules).items())
        ],
        "cycles": [{"modules": cycle} for cycle in cycles(modules)],
        "two_way_domains": [
            {"between": list(pair), "note": "each depends on the other"}
            for pair in two_way_domains(modules)
        ],
        "entrypoints": [
            {"id": name, "purpose": purpose}
            for name, purpose in sorted(ENTRYPOINTS.items())
            if name in modules
        ],
        "most_depended_on": [
            {"id": name, "depended_on_by": count}
            for name, count in sorted(fan_in.items(), key=lambda item: -item[1])[:12]
        ],
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive the project graph from the code")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)

    modules = scan()
    found_cycles = cycles(modules)
    broken = violations(modules, load_rules())

    if args.json:
        print(json.dumps({"cycles": found_cycles, "violations": broken}, indent=2))
    else:
        print(f"{len(modules)} modules, {sum(len(m['imports']) for m in modules.values())} imports")
        print(f"cycles: {len(found_cycles)}")
        for cycle in found_cycles:
            print("  " + " -> ".join(cycle))
        two_way = two_way_domains(modules)
        print(f"two-way domain dependencies: {len(two_way)}")
        for pair in two_way:
            print(f"  {pair[0]} <-> {pair[1]}")
        print(f"forbidden imports: {len(broken)}")
        for item in broken:
            print(f"  [{item['rule']}] {item['module']} imports {item['imports']} — {item['why']}")

    if not args.check:
        GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
        GRAPH_PATH.write_text(render(modules), encoding="utf-8")
        print(f"wrote {GRAPH_PATH.relative_to(ROOT)}")
    return 1 if (found_cycles or broken) else 0  # two-way domains are reported, not gated


if __name__ == "__main__":
    sys.exit(main())
