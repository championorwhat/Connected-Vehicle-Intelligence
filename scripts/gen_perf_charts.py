"""Render the M14 load-test charts as SVG from the evidence JSON (no plotting dependency).

    uv run python scripts/gen_perf_charts.py      # writes docs/images/m14-*.svg

Charts follow the repository's data-viz rules: one y-axis per panel (two measures get
two panels on a shared time axis), at most three categorical series per panel (the
first three validated palette slots), 2 px lines, direct labels at line ends plus a
legend, recessive grid, text in ink colours, never the series colour.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/load-tests"
OUT = ROOT / "docs/images"

SURFACE, INK, INK_2, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3", "#b9b8b3"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]  # palette slots 1-3 (validated all-pairs)
SHADE = "#f1f0ec"  # neutral band (burst window), not a status colour
W, PANEL_H, LEFT, RIGHT, TOP, GAP = 760, 190, 64, 150, 44, 46


def nice_axis(v: float, ticks: int = 4) -> tuple[float, float]:
    """(max, step) with a round step (1, 2, 2.5 or 5 x 10^n) and about `ticks` intervals."""
    if v <= 0:
        return 1.0, 0.25
    raw = v / ticks
    exp = float(10 ** math.floor(math.log10(raw)))
    step = next(m * exp for m in (1, 2, 2.5, 5, 10) if m * exp >= raw)
    return math.ceil(v / step) * step, step


def fmt(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:g}K"
    return f"{v:g}"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


def chart(title: str, subtitle: str, x_label: str, panels: list[dict[str, Any]],
          x_step: float, band: tuple[float, float] | None = None) -> str:  # fmt: skip
    """panels: [{"label": y-axis title, "series": [(name, [(x, y), ...])], "ref": (y, text)}]"""
    height = TOP + 30 + len(panels) * (PANEL_H + GAP) + 10
    xs = [float(x) for p in panels for item in p["series"] for x, _ in item[1]]
    x0, x1 = min(xs), max(xs)
    plot_w = W - LEFT - RIGHT

    def sx(x: float) -> float:
        return LEFT + (x - x0) / (x1 - x0) * plot_w

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
           f'viewBox="0 0 {W} {height}" font-family="Inter, Helvetica, Arial, sans-serif" '
           f'role="img" aria-label="{esc(title)}">',
           f'<rect width="{W}" height="{height}" fill="{SURFACE}"/>',
           f'<text x="{LEFT}" y="22" font-size="15" font-weight="600" fill="{INK}">'
           f"{esc(title)}</text>",
           f'<text x="{LEFT}" y="40" font-size="12" fill="{INK_2}">'
           f"{esc(subtitle)}</text>"]  # fmt: skip
    for i, panel in enumerate(panels):
        top = TOP + 30 + i * (PANEL_H + GAP)
        ymax, ystep = nice_axis(max([y for item in panel["series"] for _, y in item[1]]
                                    + [panel.get("ref", (0, ""))[0]]))  # fmt: skip

        def sy(y: float, top: float = top, ymax: float = ymax) -> float:
            return top + PANEL_H - y / ymax * PANEL_H

        if band:
            width = sx(band[1]) - sx(band[0])
            out.append(f'<rect x="{sx(band[0]):.1f}" y="{top}" width="{width:.1f}"'
                       f' height="{PANEL_H}" fill="{SHADE}"/>')  # fmt: skip
            if i == 0:
                out.append(f'<text x="{sx(band[0]) + 4:.1f}" y="{top + 12}" font-size="11" '
                           f'fill="{INK_2}">burst window</text>')  # fmt: skip
        for k in range(round(ymax / ystep) + 1):
            y = ystep * k
            out.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{sy(y):.1f}" y2="{sy(y):.1f}" '
                       f'stroke="{GRID if k else AXIS}" stroke-width="1"/>')  # fmt: skip
            out.append(f'<text x="{LEFT - 8}" y="{sy(y) + 4:.1f}" font-size="11" fill="{INK_2}" '
                       f'text-anchor="end">{fmt(y)}</text>')  # fmt: skip
        out.append(f'<text x="{LEFT}" y="{top - 8}" font-size="12" fill="{INK}">'
                   f"{esc(panel['label'])}</text>")  # fmt: skip
        if "ref" in panel:
            ry, rtext = panel["ref"]
            out.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{sy(ry):.1f}" y2="{sy(ry):.1f}" '
                       f'stroke="{INK_2}" stroke-width="1" stroke-dasharray="4 4"/>')  # fmt: skip
            out.append(f'<text x="{W - RIGHT + 6}" y="{sy(ry) + 4:.1f}" font-size="11" '
                       f'fill="{INK_2}">{esc(rtext)}</text>')  # fmt: skip
        ends: list[tuple[float, str, str]] = []
        for j, item in enumerate(panel["series"]):
            name, pts = item[0], item[1]
            color = item[2] if len(item) > 2 else SERIES[j]  # colour follows the entity
            path = " ".join(f"{'M' if n == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}"
                            for n, (x, y) in enumerate(pts))  # fmt: skip
            out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
                       f'stroke-linejoin="round" stroke-linecap="round"/>')  # fmt: skip
            ends.append((sy(pts[-1][1]), name, color))
        # direct labels at the line ends, nudged apart so they never collide
        ends.sort()
        placed: list[float] = []
        for y, name, color in ends:
            y = max(y, placed[-1] + 14) if placed else y
            placed.append(y)
            out.append(f'<circle cx="{W - RIGHT + 8}" cy="{y - 4:.1f}" r="4" fill="{color}"/>')
            out.append(f'<text x="{W - RIGHT + 16}" y="{y:.1f}" font-size="11" fill="{INK}">'
                       f"{esc(name)}</text>")  # fmt: skip
    last_bottom = TOP + 30 + len(panels) * (PANEL_H + GAP) - GAP
    for k in range(int((x1 - x0) // x_step) + 1):
        x = x0 + k * x_step
        out.append(f'<text x="{sx(x):.1f}" y="{last_bottom + 16}" font-size="11" fill="{INK_2}" '
                   f'text-anchor="middle">{x:.0f}</text>')  # fmt: skip
    out.append(f'<text x="{LEFT + plot_w / 2:.1f}" y="{last_bottom + 32}" font-size="11" '
               f'fill="{INK_2}" text-anchor="middle">{esc(x_label)}</text>')  # fmt: skip
    out.append("</svg>")
    return "\n".join(out) + "\n"


Points = list[tuple[float, float]]


def series(samples: list[dict[str, Any]], key: str) -> Points:
    return [(float(s["t"]), float(s[key])) for s in samples if s.get(key) is not None]


def mem(samples: list[dict[str, Any]], container: str) -> Points:
    return [(s["t"] / 60, s["docker"][container]["mem_mib"]) for s in samples
            if container in s.get("docker", {})]  # fmt: skip


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    burst = json.loads((EVIDENCE / "m14-burst-3x.json").read_text())
    s = burst["samples"]
    high = [x["t"] for x in s if (x.get("delivered_ev_s") or 0) > 13000]  # burst on the wire
    peak = max(s, key=lambda x: x.get("detector_lag") or 0)
    drained = next(x["t"] for x in s
                   if x["t"] > peak["t"] and (x.get("detector_lag") or 1e12) < 5000)  # fmt: skip
    (OUT / "m14-burst.svg").write_text(chart(
        f"A burst: the backlog peaks at {peak['detector_lag'] / 1000:.0f}K messages and drains "
        f"{drained - max(high):.0f} s after input returns to normal",
        "100K vehicles at 10K events/s, 3x burst requested; the simulator delivered ~22K/s on "
        "the shared 4 vCPUs",
        "seconds since the simulator started",
        [{"label": "messages per second", "series": [
            ("delivered", series(s, "delivered_ev_s")),
            ("normalizer", series(s, "normalizer_msg_s")),
            ("detector", series(s, "detector_msg_s"))]},
         {"label": "detector backlog (messages)", "series": [
            ("detector backlog", series(s, "detector_lag"), SERIES[2])]}],
        x_step=60, band=(min(high), max(high)),
    ))  # fmt: skip
    soak_file = EVIDENCE / "m14-soak-30min.json"
    if not soak_file.exists():
        print("wrote docs/images/m14-burst.svg (no soak evidence yet)")
        return 0
    soak = json.loads(soak_file.read_text())
    s = soak["samples"]
    behind = [(t / 60, y) for t, y in series(s, "detector_seconds_behind")]
    (OUT / "m14-soak.svg").write_text(chart(
        "30-minute soak: memory stays flat, the pipeline stays current",
        "100K vehicles at 10K events/s",
        "minutes",
        [{"label": "container memory (MiB)", "series": [
            ("kafka", mem(s, "kafka-1")), ("clickhouse", mem(s, "clickhouse-1")),
            ("detector", mem(s, "detector-1"))]},
         {"label": "detector seconds behind the stream", "ref": (60, "60 s SLO"),
          "series": [("detector", behind, SERIES[2])]}],
        x_step=5,
    ))  # fmt: skip
    print("wrote docs/images/m14-burst.svg, docs/images/m14-soak.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
