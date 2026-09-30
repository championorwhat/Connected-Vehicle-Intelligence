"""Dashboards and alerts must only use metrics that services really export.

Monitoring rots silently: a renamed metric leaves a panel empty and an alert that
can never fire. This test reads every metric the code defines (AST) and checks
each PromQL expression in the Grafana dashboards and Prometheus rules against it.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "infra/monitoring/prometheus/rules/prognos.rules.yml"
DASHBOARDS = ROOT / "infra/monitoring/grafana/dashboards"
RUNBOOKS = ROOT / "docs/observability/runbooks.md"
NAME = re.compile(r"\bprognos[_:][a-z0-9_:]+")
SUFFIXES = {"Counter": ["_total"], "Histogram": ["_bucket", "_count", "_sum"],
            "Gauge": [""], "Summary": ["_count", "_sum"]}  # fmt: skip


def exported_metrics() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("**/*.py"):
        if {".venv", "node_modules"} & set(path.parts) or "tests" in path.parts:
            continue
        source = path.read_text()
        # simulator: custom collector families, e.g. CounterMetricFamily("prognos_sim_x", ...)
        family = r'(Counter|Gauge)MetricFamily\(\s*"(prognos_[a-z_]+)"'
        for kind, name in re.findall(family, source):
            names.update(name + s for s in SUFFIXES[kind])
        for name in re.findall(r'\("(prognos_sim_[a-z_]+)", "[a-z_]+", "', source):
            names.add(name + "_total")
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) in SUFFIXES
                    and node.args and isinstance(node.args[0], ast.Constant)):  # fmt: skip
                base = str(node.args[0].value)
                names.update(base + s for s in SUFFIXES[node.func.id])  # type: ignore[attr-defined]
    return names


def recording_rules() -> set[str]:
    doc = yaml.safe_load(RULES.read_text())
    return {r["record"] for g in doc["groups"] for r in g["rules"] if "record" in r}


def rule_expressions() -> list[tuple[str, str]]:
    doc = yaml.safe_load(RULES.read_text())
    return [(r.get("record") or r["alert"], r["expr"]) for g in doc["groups"] for r in g["rules"]]


def dashboard_expressions() -> list[tuple[str, str]]:
    out = []
    for path in DASHBOARDS.glob("*.json"):
        for panel in json.loads(path.read_text())["panels"]:
            for target in panel.get("targets", []):
                out.append((f"{path.name}: {panel['title']}", target["expr"]))
    return out


def test_every_referenced_metric_is_exported() -> None:
    known = exported_metrics() | recording_rules()
    assert "prognos_detector_alert_latency_seconds_bucket" in known  # the scan works
    missing = [
        (where, name)
        for where, expr in rule_expressions() + dashboard_expressions()
        for name in NAME.findall(expr)
        if name not in known
    ]
    assert not missing, missing


def test_every_alert_has_a_runbook_section() -> None:
    doc = yaml.safe_load(RULES.read_text())
    headings = {
        re.sub(r"[^a-z0-9 -]", "", line.lstrip("# ").strip().lower()).replace(" ", "-")
        for line in RUNBOOKS.read_text().splitlines()
        if line.startswith("## ")
    }
    for group in doc["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            link = rule["annotations"]["runbook"]
            assert link.startswith("docs/observability/runbooks.md#"), rule["alert"]
            assert link.split("#", 1)[1] in headings, (rule["alert"], link)
            assert rule["labels"]["severity"] in {"page", "ticket"}, rule["alert"]


def test_dashboards_match_their_generator() -> None:
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts/gen_dashboards.py")
    assert spec is not None
    assert spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    board = gen.overview().json()
    committed = json.loads((DASHBOARDS / f"{board['uid']}.json").read_text())
    assert committed == board, "run: uv run python scripts/gen_dashboards.py"


def test_every_service_is_scraped() -> None:
    config = yaml.safe_load((ROOT / "infra/monitoring/prometheus/prometheus.yml").read_text())
    jobs = {job["job_name"] for job in config["scrape_configs"]}
    expected = {"simulator", "normalizer", "detector", "sink", "planner", "radar", "scorer",
                "api", "clickhouse"}  # fmt: skip
    assert expected <= jobs, expected - jobs
    assert config["rule_files"] == ["/etc/prometheus/rules/*.rules.yml"]
