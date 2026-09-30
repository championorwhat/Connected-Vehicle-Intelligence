"""The documents must point at things that exist (M17).

Every measured claim in the Solution Document links to an evidence file, and every
feature in the traceability CSV names its code. A renamed file or heading silently
breaks those links, so this test resolves each relative link (and its #anchor) in the
README and docs/, checks that each code path in the CSV exists, and checks that the
Solution Document's feature table matches the CSV.
"""

from __future__ import annotations

import csv
import importlib.util
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
CODE = re.compile(r"`[^`\n]*`")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to '-'."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*$", text, re.M)}


def links(path: Path) -> list[str]:
    text = CODE.sub("", FENCE.sub("", path.read_text(encoding="utf-8")))
    return LINK.findall(text)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc: Path) -> None:
    broken = []
    for target in links(doc):
        if re.match(r"^[a-z]+:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        dest = (doc.parent / unquote(file_part)).resolve() if file_part else doc
        if not dest.exists():
            broken.append(f"{target} (missing file)")
        elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            broken.append(f"{target} (no heading #{anchor})")
    assert not broken, f"{doc.relative_to(ROOT)}: " + ", ".join(broken)


def test_traceability_code_paths_exist() -> None:
    rows = csv.DictReader((ROOT / "docs/feature-traceability.csv").open(encoding="utf-8"))
    # a path may carry a note, e.g. "engine.py (firmware_defect)"
    paths = [
        (r["Feature ID"], p.split(" (")[0].strip()) for r in rows for p in r["Code Path"].split(";")
    ]
    missing = [f"{fid}: {p}" for fid, p in paths if p and not (ROOT / p).exists()]
    assert not missing, missing


def test_solution_document_feature_table_matches_csv() -> None:
    spec = importlib.util.spec_from_file_location(
        "sync_solution_document", ROOT / "scripts/sync_solution_document.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    doc = module.DOC.read_text(encoding="utf-8")
    assert module.render(doc) == doc, "run: uv run python scripts/sync_solution_document.py"


def test_solution_document_has_the_17_template_sections() -> None:
    text = (ROOT / "docs/solution-document/solution-document.md").read_text(encoding="utf-8")
    numbered = re.findall(r"^## (\d+)\. ", text, re.M)
    assert numbered == [str(n) for n in range(1, 18)]


def test_audit_summary_matches_its_rows() -> None:
    text = (ROOT / "docs/audit/final-audit.md").read_text(encoding="utf-8")
    counts = {"Met": 0, "Partial": 0, "Not met": 0}
    for row in re.findall(r"^\| [A-Z]\d+ \|.*\|$", text, re.M):
        status = row.rstrip("|").split("|")[-1]
        counts[
            "Not met" if "Not met" in status else "Partial" if "Partial" in status else "Met"
        ] += 1
    total = sum(counts.values())
    met, partial, not_met = counts["Met"], counts["Partial"], counts["Not met"]
    expected = f"| **Total ({total})** | **{met}** | **{partial}** | **{not_met}** |"
    assert expected in text, f"summary should read {expected}"
