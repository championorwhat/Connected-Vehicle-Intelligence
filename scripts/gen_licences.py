"""Open-source declaration from the SBOMs (M17): uv run python scripts/gen_licences.py

Inputs:
  evidence/sbom/prognos-*.cdx.json   CycloneDX SBOMs of the built images, made with
                                     `trivy image --format cyclonedx prognos/<image>:local`
  apps/web/package-lock.json         the dashboard's runtime (non-dev) packages
Output: docs/open-source.md (Python and web packages that ship, with their licences, plus
the third-party service images, whose licences are listed by hand below).
"""

from __future__ import annotations

import json
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SBOMS = sorted((ROOT / "evidence/sbom").glob("prognos-*.cdx.json"))
OUT = ROOT / "docs/open-source.md"

# Third-party images run by docker compose. Their licences are not in the SBOMs above
# (only nginx-unprivileged and dbmate carry an OCI licence label), so they are stated here
# from each project's published licence.
SERVICES = [
    ("Apache Kafka", "apache/kafka:4.1.0", "Apache-2.0", "message broker"),
    ("PostgreSQL", "postgres:17-alpine", "PostgreSQL License", "relational store"),
    ("ClickHouse", "clickhouse/clickhouse-server:25.8-alpine", "Apache-2.0", "telemetry store"),
    ("Redis", "redis:7.4-alpine", "RSALv2 / SSPLv1 (source-available, not OSI open source)",
     "live state, pub/sub, rate limits"),
    ("nginx (unprivileged image)", "nginxinc/nginx-unprivileged:1.29-alpine",
     "BSD-2-Clause (nginx); image Apache-2.0", "dashboard web server"),
    ("dbmate", "amacneil/dbmate:2.28", "MIT", "schema migrations"),
    ("Prometheus", "prom/prometheus:v3.5.0", "Apache-2.0", "metrics (optional profile)"),
    ("Grafana", "grafana/grafana:12.1.0", "AGPL-3.0", "dashboards (optional profile)"),
    ("Trivy", "aquasec/trivy:0.67.2", "Apache-2.0", "security scans (CI)"),
    ("Gitleaks", "zricethezav/gitleaks:v8.28.0", "MIT", "secret scan (CI)"),
    ("Semgrep CE", "semgrep (CI)", "LGPL-2.1", "static analysis (CI)"),
]  # fmt: skip


def licence(component: dict[str, Any]) -> str:
    names = []
    for entry in component.get("licenses") or []:
        lic = entry.get("license", {})
        name = lic.get("id") or lic.get("name") or entry.get("expression")
        if name:
            names.append(name)
    return ", ".join(dict.fromkeys(names)) or "not declared in package metadata"


def installed_licence(name: str) -> str:
    """Licence from an installed package's own metadata (for images that were not scanned)."""
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "not installed locally"
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [c.rsplit(" :: ", 1)[-1] for c in meta.get_all("Classifier") or []
                   if c.startswith("License :: OSI Approved")]  # fmt: skip
    short = meta.get("License") or ""
    return ", ".join(classifiers) or (short if 0 < len(short) < 60 else "see package metadata")


def ml_from_lock() -> list[tuple[str, str]]:
    """Runtime packages of the ML image from uv.lock, when its image SBOM is missing."""
    out = subprocess.run(
        ["uv", "export", "--package", "prognos-ml", "--no-dev", "--no-hashes",  # noqa: S607
         "--no-emit-workspace", "--no-header", "--no-annotate"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    pins = [line.split(";")[0].strip() for line in out.splitlines()
            if "==" in line and "sys_platform == 'win32'" not in line]  # fmt: skip
    return [(pin.split("==")[0].lower(), pin.split("==")[1]) for pin in pins]


def main() -> int:
    python: dict[tuple[str, str], tuple[str, set[str]]] = {}
    os_packages: dict[str, int] = {}
    for path in SBOMS:
        image = path.name.removeprefix("prognos-").removesuffix(".cdx.json")
        for c in json.loads(path.read_text()).get("components", []):
            purl = c.get("purl") or ""
            if purl.startswith("pkg:pypi/"):
                if c["name"].startswith("prognos"):
                    continue  # this repository (MIT)
                key = (c["name"].lower().replace("_", "-"), c.get("version", ""))
                python.setdefault(key, (licence(c), set()))[1].add(image)
            elif purl.startswith(("pkg:deb/", "pkg:apk/", "pkg:rpm/")):
                os_packages[image] = os_packages.get(image, 0) + 1

    ml_note = ""
    if not any(p.name == "prognos-ml.cdx.json" for p in SBOMS):
        for name, version in ml_from_lock():
            entry = python.setdefault((name, version), (installed_licence(name), set()))
            entry[1].add("ml (lock)")
        ml_note = (
            "The `ml` image (scorer) could not be rebuilt for scanning here, so its packages "
            "come from `uv.lock` and the licences from the installed packages' own metadata "
            '(marked "ml (lock)").'
        )

    lock = json.loads((ROOT / "apps/web/package-lock.json").read_text())["packages"]
    web = sorted((k.split("node_modules/")[-1], v.get("version", ""), v.get("license", ""))
                 for k, v in lock.items() if k and not v.get("dev"))  # fmt: skip

    lines = [
        "# Open-source components",
        "",
        "Generated by `scripts/gen_licences.py` from the CycloneDX SBOMs in",
        "[`evidence/sbom/`](../evidence/sbom/) (Trivy, built images) and the dashboard's",
        "`package-lock.json`. Prognos itself is MIT ([LICENSE](../LICENSE)).",
        "",
        "## Services run by docker compose",
        "",
        "| Component | Image | Licence | Used for |",
        "|---|---|---|---|",
        *(f"| {n} | `{i}` | {lic} | {use} |" for n, i, lic, use in SERVICES),
        "",
        "**Redis 7.4 is source-available, not open source.** Running it inside your own",
        "deployment is allowed; offering it to third parties as a managed service is not.",
        "Prognos uses only core commands (strings, sorted sets, GEOADD, pub/sub), which",
        "Valkey (BSD-3-Clause) also implements, so it could be swapped in; that swap has",
        "**not been tested**.",
        "",
        "## Python packages in the built images",
        "",
        *([ml_note, ""] if ml_note else []),
        "| Package | Version | Licence | Images |",
        "|---|---|---|---|",
        *(f"| {n} | {v} | {lic} | {', '.join(sorted(imgs))} |"
          for (n, v), (lic, imgs) in sorted(python.items())),
        "",
        "## Dashboard runtime packages (bundled into the web image)",
        "",
        "| Package | Version | Licence |",
        "|---|---|---|",
        *(f"| {n} | {v} | {lic} |" for n, v, lic in web),
        "",
        "## Operating-system packages",
        "",
        "Base-image OS packages are listed with their licences in each SBOM: "
        + ", ".join(f"{img} {n}" for img, n in sorted(os_packages.items())) + ".",
        "",
    ]  # fmt: skip
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(python)} Python, {len(web)} web packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
