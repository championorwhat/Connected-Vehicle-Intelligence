"""Fill the Solution Document's feature list from docs/feature-traceability.csv (M17).

    uv run python scripts/sync_solution_document.py          # rewrite the table in place
    uv run python scripts/sync_solution_document.py --check  # exit 1 if it is out of date

The CSV is the single source of truth (it also names the test and the evidence for each
feature); the document shows the columns the official template asks for.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "docs/feature-traceability.csv"
DOC = ROOT / "docs/solution-document/solution-document.md"
START, END = "<!-- features:start -->", "<!-- features:end -->"
NONE = "\u2013"  # en dash for an empty cell


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def code_paths(text: str) -> str:
    return "<br>".join(f"`{p.strip()}`" for p in text.split(";") if p.strip()) or NONE


def table() -> str:
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    lines = ["| ID | Feature | User story | Priority | Status | Code path | Video |",
             "|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in rows:
        lines.append(
            f"| {r['Feature ID']} | {cell(r['Feature'])} | {cell(r['User Story'])} "
            f"| {r['Priority']} | {r['Status']} | {code_paths(r['Code Path'])} "
            f"| {cell(r['Demo Timestamp']) or NONE} |"
        )
    done = sum(r["Status"] == "Done" for r in rows)
    lines += ["", f"{len(rows)} features: {done} Done, {len(rows) - done} Partial or Planned."]
    return "\n".join(lines)


def render(doc: str) -> str:
    head, rest = doc.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{table()}\n{END}{tail}"


def main() -> int:
    doc = DOC.read_text(encoding="utf-8")
    new = render(doc)
    if "--check" in sys.argv[1:]:
        if new != doc:
            print("feature list is out of date: run scripts/sync_solution_document.py")
            return 1
        return 0
    DOC.write_text(new, encoding="utf-8")
    print(f"updated {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
