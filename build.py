#!/usr/bin/env python3
"""Build the static site: content/*.md + content/matrix.json -> docs/ (GitHub Pages: main /docs).
Usage: python3 build.py   (requires the `markdown` package)"""
import json, re, html, pathlib, datetime
import markdown

ROOT = pathlib.Path(__file__).parent
CONTENT, OUT = ROOT / "content", ROOT / "docs"
SITE_TITLE = "硬科技一级市场趋势观察"

def parse(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    meta = dict(l.split(": ", 1) for l in m.group(1).splitlines())
    return meta, m.group(2)

def md(s):
    return markdown.markdown(s, extensions=["tables"])

pages = [parse(p) for p in sorted(CONTENT.glob("[0-9]*.md"))]
overview = next(p for p in pages if p[0]["slug"] == "index")
tracks = [p for p in pages if p[0]["slug"] not in ("index", "changelog-overview")]
ov_log = next(p for p in pages if p[0]["slug"] == "changelog-overview")
updated = max(p[0]["updated"] for p in pages)
built = datetime.date.today().isoformat()

def nav(active):
    items = [("index.html", "总览")] + [(f"{m['slug']}.html", m["title"]) for m, _ in tracks] + [("changelog.html", "变更记录")]
    return "".join(f'<a href="{h}"{" class=on" if t == active else ""}>{t}</a>' for h, t in items)

def page(title, body, active, page_updated):
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}｜{SITE_TITLE}</title><link rel="stylesheet" href="style.css"></head>
<body><header><div class="brand"><a href="index.html">{SITE_TITLE}</a></div><nav>{nav(active)}</nav></header>
<main><h1>{html.escape(title)}</h1><p class="meta">本页更新：{page_updated}</p>{body}</main>
<footer>研究用途，非投资建议；不构成任何证券的买卖推荐。内容均来自公开资料与经脱敏的行业判断。<br>更新日期：{page_updated}（全站最近更新 {updated}，生成于 {built}）</footer></body></html>"""

def matrix_html():
    m = json.loads((CONTENT / "matrix.json").read_text(encoding="utf-8"))
    cls = {"值得看": "go", "观察": "watch", "不看": "no"}
    head = "".join(f"<th>{h}</th>" for h in m["horizons"])
    rows = ""
    for t in m["tracks"]:
        cells = "".join(f'<td><span class="tag {cls[c[0]]}">{c[0]}</span><span class="line">{html.escape(c[1])}</span></td>' for c in t["cells"])
        rows += f'<tr><th><a href="{t["slug"]}.html">{t["name"]}</a></th>{cells}</tr>'
    return f'<h2>六条赛道 × 四个时间维度</h2><div class="tablewrap"><table class="matrix"><thead><tr><th>赛道</th>{head}</tr></thead><tbody>{rows}</tbody></table></div><p class="note">{html.escape(m["rule"])}</p>'

def split_changelog(body):
    parts = re.split(r"\n## 变更记录\n", body)
    return parts[0], (parts[1] if len(parts) > 1 else "")

OUT.mkdir(exist_ok=True)
(OUT / "style.css").write_text((ROOT / "style.css").read_text(encoding="utf-8"), encoding="utf-8")
(OUT / ".nojekyll").write_text("")
(OUT / "robots.txt").write_text("User-agent: *\nDisallow: /\n")

meta, body = overview
body = md(body).replace("<p><!--MATRIX--></p>", matrix_html()).replace("<!--MATRIX-->", matrix_html())
(OUT / "index.html").write_text(page("总览", body, "总览", meta["updated"]), encoding="utf-8")

entries = []
for line in ov_log[1].splitlines():
    if line.startswith("- "): entries.append((line[2:12], "总览", line[2:]))
for meta, body in tracks:
    main, log = split_changelog(body)
    (OUT / f"{meta['slug']}.html").write_text(page(meta["title"], md(main) + "<h2>变更记录</h2>" + md(log), meta["title"], meta["updated"]), encoding="utf-8")
    for line in log.splitlines():
        if line.startswith("- "): entries.append((line[2:12], meta["title"], line[2:]))
entries.sort(key=lambda e: e[0], reverse=True)
log_md = "\n".join(f"- **{t}**｜{html.escape(x)}" for d, t, x in entries)
(OUT / "changelog.html").write_text(page("变更记录", "<p>各赛道判断的调整按日期倒序排列。</p>" + md(log_md), "变更记录", updated), encoding="utf-8")
print("built", len(list(OUT.glob('*.html'))), "pages ->", OUT)
