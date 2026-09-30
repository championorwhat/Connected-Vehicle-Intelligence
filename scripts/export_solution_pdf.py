"""Export the Solution Document to PDF, the template's submission format (M19).

    MMDC=/path/to/mmdc CHROMIUM=/path/to/chromium \\
      uv run --with markdown-it-py python scripts/export_solution_pdf.py

- Mermaid diagrams are rendered to SVG with mermaid-cli (`MMDC`) and embedded.
- Images are embedded, so the PDF is self-contained.
- Links to files in the repository become GitHub links, so they work from the PDF.
- Chromium (`CHROMIUM`) prints the HTML to docs/solution-document/solution-document.pdf.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/solution-document/solution-document.md"
OUT = DOC.with_suffix(".pdf")
REPO = "https://github.com/championorwhat/Connected-Vehicle-Intelligence/blob/main/"
MERMAID = re.compile(r"```mermaid\n(.*?)```", re.S)
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def browser() -> str:
    """CHROMIUM if set, else Google Chrome on macOS, else `chromium` on the PATH."""
    return os.environ.get("CHROMIUM") or (MAC_CHROME if Path(MAC_CHROME).exists() else "chromium")


CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font: 10pt/1.45 "DejaVu Sans", Helvetica, Arial, sans-serif; color: #111; }
h1 { font-size: 20pt; margin: 0 0 8pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 2pt;
     break-after: avoid; }
h2:not(:first-of-type) { break-before: page; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 8pt; }
th, td { border: 1px solid #bbb; padding: 3pt 4pt; vertical-align: top; text-align: left;
         overflow-wrap: break-word; }
th { background: #f0f0f0; white-space: nowrap; }
td code { word-break: break-all; }
tr { break-inside: avoid; }
code { font: 8.5pt "DejaVu Sans Mono", Menlo, monospace; background: #f4f4f4; padding: 0 2pt; }
pre { background: #f4f4f4; padding: 6pt; font-size: 8pt; white-space: pre-wrap;
      break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; }
.diagram { text-align: center; margin: 8pt 0; break-inside: avoid; }
.diagram img { max-height: 190mm; }
a { color: #1a55b0; text-decoration: none; }
blockquote { border-left: 3px solid #bbb; margin: 6pt 0; padding: 0 8pt; color: #333; }
"""


def slug(heading: str) -> str:
    """GitHub's heading anchor, so the table of contents links work in the PDF."""
    text = re.sub(r"<[^>]+>", "", heading).strip().lower()
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def data_uri(path: Path) -> str:
    kind = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg"}[path.suffix]
    return f"data:{kind};base64,{base64.b64encode(path.read_bytes()).decode()}"


def render_diagrams(text: str, work: Path) -> str:
    mmdc = shlex.split(os.environ.get("MMDC", "npx -y @mermaid-js/mermaid-cli@11"))
    config = work / "puppeteer.json"
    chromium = browser()
    settings: dict[str, object] = {"args": ["--no-sandbox"]}
    if chromium != "chromium":
        settings["executablePath"] = chromium
    config.write_text(json.dumps(settings))

    def one(match: re.Match[str]) -> str:
        n = len(list(work.glob("d*.mmd")))
        src, svg = work / f"d{n}.mmd", work / f"d{n}.svg"
        src.write_text(match.group(1))
        subprocess.run([*mmdc, "-p", str(config), "-i", str(src), "-o", str(svg), "-b", "white"],  # noqa: S603
                       check=True, capture_output=True)  # fmt: skip
        return f'<div class="diagram"><img src="{data_uri(svg)}" alt="diagram"></div>\n'

    return MERMAID.sub(one, text)


def fix_links(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        attr, target = match.group(1), match.group(2)
        if re.match(r"^(https?:|mailto:|#|data:)", target):
            return match.group(0)
        path, _, anchor = target.partition("#")
        local = (DOC.parent / path).resolve()
        if attr == "src" and local.is_file():
            return f'src="{data_uri(local)}"'
        rel = local.relative_to(ROOT).as_posix()
        return f'{attr}="{REPO}{rel}{"#" + anchor if anchor else ""}"'

    return re.sub(r'(href|src)="([^"]+)"', repl, html)


def anchor(match: re.Match[str]) -> str:
    tag, inner = match.group(1), match.group(2)
    return f'<{tag} id="{slug(inner)}">{inner}</{tag}>'


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        text = render_diagrams(DOC.read_text(encoding="utf-8"), work)
        # CommonMark, as GitHub renders it (lists without blank lines, 2-space nesting)
        body = MarkdownIt("commonmark", {"html": True}).enable("table").render(text)
        body = re.sub(r"<(h[1-4])>(.*?)</\1>", anchor, body)
        page = work / "solution-document.html"
        page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Prognos: "
                        f"Solution Document</title><style>{CSS}</style></head>"
                        f"<body>{fix_links(body)}</body></html>", encoding="utf-8")  # fmt: skip
        chromium = browser()
        subprocess.run([chromium, "--headless", "--no-sandbox", "--no-pdf-header-footer",  # noqa: S603
                        f"--print-to-pdf={OUT}", page.as_uri()],
                       check=True, capture_output=True)  # fmt: skip
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
