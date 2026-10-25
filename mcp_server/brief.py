"""Render a case's evidence brief as self-contained HTML (screenshots embedded) and Markdown.

Formatting is deterministic: the Presentation agent chooses the headline, sections,
and which verified findings to cite; quotes, page numbers, links, and screenshots
are filled in from the case board, never retyped by a model.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from .store import PROJECT_DIR, rel

CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1c2430;max-width:960px;margin:32px auto;padding:0 20px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 6px;border-bottom:1px solid #dde3ea;padding-bottom:4px}
.req{color:#5b6775;margin:0 0 18px}.headline{font-size:17px;background:#f2f6fa;border-left:4px solid #1f5fa8;padding:10px 14px}
.card{border:1px solid #dde3ea;border-radius:8px;padding:12px 14px;margin:12px 0}
.claim{font-weight:600}.quote{font-family:Georgia,serif;color:#33404f;margin:6px 0;border-left:3px solid #c9a227;padding-left:10px}
.cite{font-size:13px;color:#5b6775}.cite a{color:#1f5fa8}.shot{max-width:100%;border:1px solid #ccd3db;margin-top:8px}
.caveats{color:#5b6775;font-size:14px}.foot{color:#8a96a3;font-size:12px;margin-top:30px}
"""


def _img(path: str | None) -> str:
    if not path:
        return ""
    p = PROJECT_DIR / path
    if not p.exists():
        return ""
    data = base64.b64encode(p.read_bytes()).decode()
    return f'<img class="shot" alt="Highlighted source page" src="data:image/png;base64,{data}">'


def write_brief(body: dict[str, Any], stem: Path) -> dict[str, str]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    e = html.escape
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>Evidence brief {e(body['case_id'])}</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>PinCite evidence brief</h1><p class='req'>Request: {e(body['request'])}</p>",
             f"<p class='headline'>{e(body['headline'])}</p>",
             (f"<p class='req'>Sources dated {e(body.get('oldest_source_date') or '')} to "
              f"{e(body.get('newest_source_date') or '')} · newest evidence listed first</p>"
              if body.get("newest_source_date") else "")]
    md = [f"# Evidence brief: {body['case_id']}", "", f"**Request:** {body['request']}", "", f"**{body['headline']}**", ""]
    if body.get("newest_source_date"):
        md += [f"_Sources dated {body.get('oldest_source_date')} to {body['newest_source_date']} · newest first_", ""]
    for s in body["sections"]:
        parts.append(f"<h2>{e(s['heading'])}</h2><p>{e(s['summary'])}</p>")
        md += [f"## {s['heading']}", "", s["summary"], ""]
        for f in s["findings"]:
            c = f["citation"]
            link = f" <a href='{e(c['pdf_link'])}'>open PDF at this page</a>" if c.get("pdf_link") else ""
            land = f" · <a href='{e(c['landing_url'])}'>report page</a>" if c.get("landing_url") else ""
            parts.append(
                f"<div class='card'><div class='claim'>{e(f['claim'])}</div>"
                f"<div class='quote'>“{e(f['quote'])}”</div>"
                f"<div class='cite'>[{f['finding_id']}] {e(c['text'])}.{link}{land}</div>{_img(f.get('evidence_path'))}</div>")
            md += [f"- **{f['claim']}**", f"  > \"{f['quote']}\"",
                   f"  — {c['text']}" + (f" ([PDF]({c['pdf_link']}))" if c.get("pdf_link") else ""),
                   *( [f"  ![evidence](../../{f['evidence_path']})"] if f.get("evidence_path") else []), ""]
    if body.get("caveats"):
        parts.append("<h2>Caveats</h2><ul class='caveats'>" + "".join(f"<li>{e(c)}</li>" for c in body["caveats"]) + "</ul>")
        md += ["## Caveats", "", *[f"- {c}" for c in body["caveats"]], ""]
    u = body.get("usage", {})
    parts.append(f"<p class='foot'>Case {e(body['case_id'])} · searches {u.get('search_rounds')} · reports opened "
                 f"{u.get('reports_opened')} · pages read {u.get('pages_read')} · every quote was machine-checked "
                 "against its PDF page before it was allowed into this brief.</p></body></html>")
    html_path, md_path, json_path = stem.with_suffix(".html"), stem.with_suffix(".md"), stem.with_suffix(".json")
    html_path.write_text("".join(parts), encoding="utf-8")
    md_path.write_text("\n".join(md), encoding="utf-8")
    json_path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"html": rel(html_path), "markdown": rel(md_path)}
