#!/usr/bin/env python3
"""Generate and validate a local static preview before replacing docs/."""
import contextlib
import datetime
import html
import io
import json
import pathlib
import re
import shutil
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent
SITE_TITLE = '硬科技一级市场趋势观察'
DISCLAIMER = '阶段码为产业结构研究口径，非投资建议，不反映任何持仓。'
STAGE_LEGEND = '展示码主码后的括号表示该赛道细分码的跨度，不是单一精确读数；各细分的推定码见分布图。'
CATEGORIES = {'国产替代': 'domestic', '自主可控': 'sovereign', '未来产业': 'future'}
LABELS = {'值得看': 'go', '观察': 'watch', '不看': 'no'}
CODES = ['S0', 'S1a', 'S1b', 'S2', 'S3a', 'S3b']
CSS_VERSION = '20261112-legend1'
STAGE_DEFINITIONS = {
    'S0': ('始局前', ('萌芽：国产处于研发或样品，无量产订单', '有需求、无能力，依赖进口或空白', '科学验证：路线未收敛，在实验室或论文')),
    'S1a': ('始局', ('替代导入：首批认证或小批量，靠安全诉求而非性价比', '从无到有：首次实现（首飞成功、首颗入轨、型号定型）', '工程验证：首个工程节点')),
    'S1b': ('始局', ('替代放量：国内份额快速上升，仍以内需为主', '成体系：批量交付、组网、多家供给', '早期商业化：订单可重复')),
    'S2': ('现局', ('国产优化：成本、良率 / 性能追平龙头', '降本商业化：单位成本向国际一线收敛，商业客户成为主力', '并跑领跑：中国头部达到全球前列')),
    'S3a': ('终局', ('全球输出：借产业链与成本优势出口', '对外提供：为海外客户提供发射、卫星或服务', '标准与生态输出：掌握话语权')),
    'S3b': ('终局', ('内卷出清：增量见顶，价格战、过剩', '成熟出清：发射价格战、产能过剩、民营整合', '商品化：成本曲线变平，价格战、出清')),
}
CATEGORY_COLORS = {'domestic': '#245fc4', 'sovereign': '#087d73', 'future': '#7846ba', 'neutral': '#606a7c'}
JUDGMENT_COLORS = {'go': ('#8c4b12', '#fff0ce'), 'watch': ('#685913', '#f5f1d9'),
                   'no': ('#606974', '#e9edf2'), 'invalid': ('#606974', '#e9edf2')}


class BuildError(Exception):
    def __init__(self, path, kind):
        self.path, self.kind = path, kind


def esc(value):
    return html.escape(str(value), quote=True)


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def load_matrix(root):
    path = root / 'content/matrix.json'
    if not path.is_file():
        raise BuildError('content/matrix.json', 'MissingFile')
    try:
        raw = path.read_text(encoding='utf-8')
    except Exception:
        raise BuildError('content/matrix.json', 'ReadError') from None
    if not raw.strip():
        raise BuildError('content/matrix.json', 'EmptyFile')
    try:
        m = json.loads(raw)
    except Exception:
        raise BuildError('content/matrix.json', 'InvalidJSON') from None
    if not isinstance(m, dict) or not all(k in m for k in ('horizons', 'tracks')):
        raise BuildError('content/matrix.json', 'MissingFields')
    if (not isinstance(m['horizons'], list) or not m['horizons']
            or not all(isinstance(h, str) and h for h in m['horizons'])
            or not isinstance(m['tracks'], list) or not m['tracks']):
        raise BuildError('content/matrix.json', 'InvalidStructure')
    for t in m['tracks']:
        if (not isinstance(t, dict) or not isinstance(t.get('name'), str)
                or not isinstance(t.get('slug'), str)
                or not re.fullmatch(r'[a-z][a-z0-9-]*', t['slug'])):
            raise BuildError('content/matrix.json', 'InvalidTrack')
    return m


def read_page(path):
    try:
        raw = path.read_text(encoding='utf-8')
        match = re.fullmatch(r'---\n(.*?)\n---\n(.*)', raw, re.S)
        meta = dict(line.split(': ', 1) for line in match[1].splitlines())
        if not all(k in meta for k in ('title', 'slug', 'updated')):
            raise ValueError
        if not re.fullmatch(r'[a-z][a-z0-9-]*', meta['slug']):
            raise ValueError
        return meta, match[2]
    except Exception:
        raise BuildError('content/' + path.name, 'InvalidPage') from None


def cells_for(t, count):
    cells = t.get('cells')
    if not isinstance(cells, list) or len(cells) != count:
        return [None] * count
    return [c if isinstance(c, list) and len(c) == 2
            and isinstance(c[0], str) and c[0] in LABELS
            and isinstance(c[1], str) else None for c in cells]


def rank(cell, count):
    match = re.match(r'^第\s*(\d+)\s*[｜|]', cell[1]) if cell else None
    value = int(match[1]) if match else 0
    return value if 1 <= value <= count else None


def has_stage(t):
    return (isinstance(t.get('category'), list) and bool(t['category'])
            and all(isinstance(c, str) for c in t['category'])
            and isinstance(t.get('stage_display'), str) and bool(t['stage_display'])
            and isinstance(t.get('stage_segments'), list) and bool(t['stage_segments']))


def scroll(body):
    return '<p class="scroll-hint">宽表与图表可左右滑动 ↔</p><div class="tablewrap" tabindex="0" role="region" aria-label="可左右滑动的图表">' + body + '</div>'


def svg_open(width, height, label, marker):
    return f'<svg xmlns="http://www.w3.org/2000/svg" class="chart {marker}" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{esc(label)}"><title>{esc(label)}</title>'


def txt(x, y, value, extra='', fill='#202d40', title=None):
    tooltip = f'<title>{esc(title)}</title>' if title is not None else ''
    return f'<text x="{x}" y="{y}" fill="{fill}" {extra}>{tooltip}{esc(value)}</text>'


def category_badges(categories):
    badges = []
    for c in categories:
        cls = CATEGORIES.get(c, 'neutral')
        color = CATEGORY_COLORS[cls]
        shape = (f'<path d="M 6 1 L 11 6 6 11 1 6 Z" fill="#fff" stroke="{color}" stroke-width="2"/>'
                 if cls == 'neutral' else f'<circle cx="6" cy="6" r="4" fill="{color}" stroke="{color}" stroke-width="1"/>')
        badges.append(f'<span class="category {cls}" style="display:inline-flex;align-items:center;gap:6px;margin:3px 14px 3px 0;color:{color};white-space:nowrap"><svg xmlns="http://www.w3.org/2000/svg" class="category-marker" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">{shape}</svg>{esc(c)}</span>')
    return ''.join(badges)


def judgment_badge(label):
    cls = LABELS[label]
    fg, bg = JUDGMENT_COLORS[cls]
    return f'<span class="tag {cls}" style="display:inline-block;margin-right:10px;padding:2px 7px;border-radius:5px;color:{fg};background:{bg}">{esc(label)}</span>'


def stage_meaning(code, category=None):
    level, definitions = STAGE_DEFINITIONS[code]
    meanings = [f'{cat}：{definition}' for cat, definition in zip(CATEGORIES, definitions)
                if category is None or category == cat]
    return f'{code} · {level} · ' + '；'.join(meanings)


def code_guide():
    headers = ''.join(f'<th scope="col">{number} {category_badges([cat])}</th>'
                      for number, cat in zip('①②③', CATEGORIES))
    rows = ''.join(f'<tr><th scope="row">{code}</th><td>{esc(level)}</td>'
                   + ''.join(f'<td>{esc(definition)}</td>' for definition in definitions) + '</tr>'
                   for code, (level, definitions) in STAGE_DEFINITIONS.items())
    table = '<table class="code-table"><thead><tr><th scope="col">码</th><th scope="col">层次</th>' + headers + '</tr></thead><tbody>' + rows + '</tbody></table>'
    return '''<h2>编码说明</h2><div id="code-guide"><details open><summary>怎么读类别、阶段码和颜色</summary>
<p>本页用两套公开口径：赛道类别（① 国产替代、② 自主可控、③ 未来产业）和细分阶段码 S0–S3b（始局 / 现局 / 终局）。三类共用同一套阶段码，各自定义始局、现局、终局。阶段码为产业结构研究口径，非投资建议，不反映任何持仓。按类别配色，不按阶段着色。</p>
<h3>一、三种类别，以及怎么按三问归类</h3>
<p>① 国产替代。终局 = 国产在成本和技术上超越国际龙头，再借产业链优势出口（新能源车今天的位置）。</p>
<p>② 自主可控。终局 = 体系自主可保障，成本降到国际一线可比后，对外提供产品和服务（国际发射、海外星座服务）。出口占比天然低于国产替代，S3a 的门槛相应降低。</p>
<p>③ 未来产业。终局 = 中国在关键技术指标上全球领先，并通过标准、开源生态或海外采用掌握话语权，商业化成规模。</p>
<p>三问（按顺序，命中即停）：</p>
<ol>
<li>全球有没有成熟的商业方案？没有，或中外基本同时起跑、竞争的是谁先突破和谁定标准 → ③ 未来产业。</li>
<li>有成熟方案，主要买单方是谁？国家、军方或安全需求主导采购，商业性价比不是首要驱动（「人有我也要有」） → ② 自主可控。</li>
<li>有成熟方案，主要由市场客户按性价比和认证采购，国产靠成本、性能和供应安全逐步替代 → ① 国产替代。海外方案因管制对中国不可得，但买方仍是市场客户的，归国产替代，不归自主可控。</li>
</ol>
<p>归类按细分拆，不按公司拆，也不按大行业拆。混合赛道按类别分开展示，不合并成一个码。</p>
<h3>二、阶段码 S0–S3b（始局 / 现局 / 终局）</h3>
<p>同一套码，三类的名称和一句话定义不同。展示码主码后的括号表示该赛道细分码的跨度，不是单一精确读数；各细分的推定码见分布图。</p>
''' + scroll(table) + '''
<h3>三、「推定」和「尚未定码」</h3>
<p>推定：落档依据只有二手来源。本页阶段码均为推定。</p>
<p>尚未定码：该细分还没有给出阶段码，只作观察登记，不在分布图上标实心点。与推定不同：推定是已经有码，只是依据为二手来源。</p>
<h3>四、热力图怎么看</h3>
<p>颜色表示判断，不是阶段。名次与判断均来自矩阵：第 1–2 名「''' + judgment_badge('值得看') + '''」，第 3–5 名「''' + judgment_badge('观察') + '''」，第 6–7 名「''' + judgment_badge('不看') + '''」（指不整体押注，细分环节仍可挑）。格内写原始名次。</p>
<h3>五、名次坡度图怎么看</h3>
<p>七条赛道均由矩阵里的原始「第 N」画出；越靠上名次越前。线条只表示名次随时间维度的变化，不表示阶段码。</p>
</details></div>'''


def track_guide_link(rendered):
    def add(match):
        paragraph = match[0]
        if 'href="index.html#code-guide"' in paragraph:
            return paragraph
        return paragraph[:-4] + ' <a href="index.html#code-guide">编码说明</a></p>'
    return re.sub(r'<p><strong>类别与阶段.*?</p>', add, rendered, count=1, flags=re.S)


def matrix_html(m):
    rows = []
    for t in m['tracks']:
        stage = (category_badges(t['category']) + f'<span class="line">{esc(t["stage_display"])}<br><small>推定</small></span>') if has_stage(t) else '未录入'
        cells = []
        for c in cells_for(t, len(m['horizons'])):
            cells.append(f'<td>{judgment_badge(c[0])}<span class="line">{esc(c[1])}</span></td>' if c else '<td class="invalid">数据异常</td>')
        rows.append(f'<tr><th scope="row"><a href="{esc(t["slug"])}.html">{esc(t["name"])}</a></th><td class="stage-cell">{stage}</td>{"".join(cells)}</tr>')
    head = ''.join(f'<th scope="col">{esc(h)}</th>' for h in m['horizons'])
    table = f'<table class="matrix"><caption>七条赛道 × 四个时间维度 · 原始判断</caption><thead><tr><th scope="col">赛道</th><th scope="col">类别 · 当前阶段 <a href="#code-guide">编码说明</a></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    return '<h2>七条赛道 × 四个时间维度</h2>' + scroll(table) + f'<p class="note">{esc(m.get("rule", ""))}</p>'


def heatmap(m):
    hs, ts = m['horizons'], m['tracks']
    width, height = 170 + 190 * len(hs), 65 + 64 * len(ts)
    parts = [svg_open(width, height, '七赛道 × 四时间维度热力图；颜色表示判断，格内为原始名次', 'heatmap')]
    for j, h in enumerate(hs):
        parts.append(txt(250 + 190*j, 30, h, 'text-anchor="middle"'))
    for i, t in enumerate(ts):
        y = 48 + i * 64
        parts.append(txt(12, y + 34, t['name']))
        for j, c in enumerate(cells_for(t, len(hs))):
            x = 166 + j * 190
            cls = LABELS[c[0]] if c else 'invalid'
            fg, bg = JUDGMENT_COLORS[cls]
            n = rank(c, len(ts))
            label = f'第 {n}' if n else ('名次未录入' if c else '数据异常')
            detail = f'{t["name"]} · {hs[j]} · {c[0]} · {c[1]}' if c else f'{t["name"]} · {hs[j]} · 数据异常'
            parts.append(f'<g class="heat {cls}"><title>{esc(detail)}</title><rect x="{x}" y="{y}" width="178" height="54" rx="10" fill="{bg}" stroke="{fg}" stroke-width="1" stroke-opacity=".32"/>' + txt(x+89, y+24, label, 'text-anchor="middle" class="rank"', fg) + txt(x+89, y+43, c[0] if c else '数据异常', 'text-anchor="middle" class="small"', fg) + '</g>')
    parts.append('</svg>')
    legend = '<p class="legend">' + ' '.join(judgment_badge(label) for label in LABELS) + ' 颜色表示判断；名次与判断均来自矩阵。</p>'
    return '<h2>01 / 相对吸引力热力图</h2>' + legend + scroll(''.join(parts))


def stage_chart(m):
    heading = '<h2>02 / 类别 × 阶段码分布（推定）</h2>'
    legend = '<div class="legend">' + category_badges(list(CATEGORIES) + ['按三问拆（中性空心菱形）']) + '</div>'
    legend += f'<p class="note">全部坐标码为推定。圆点表示精确码，短横与两端点表示区间或过渡；虚线空心框表示单列 / 观察细分，不是第八条赛道。按类别配色，不按阶段着色。</p><p class="note">{STAGE_LEGEND}{DISCLAIMER}</p>'
    missing = [t['name'] for t in m['tracks'] if not has_stage(t)]
    if len(missing) == len(m['tracks']):
        return heading + '<p class="empty stage-missing">阶段字段未录入</p>' + legend
    segments = []
    for t in m['tracks']:
        if has_stage(t):
            segments.extend((t['name'], s, False) for s in t['stage_segments'] if isinstance(s, dict))
    extras = m.get('extra_segments', [])
    if isinstance(extras, list):
        segments.extend(('赛道以外', s, True) for s in extras if isinstance(s, dict))
    plotted, pending = [], []
    for owner, s, extra in segments:
        code = s.get('code', '')
        codes = re.split('[–→]', code) if isinstance(code, str) else []
        if 1 <= len(codes) <= 2 and all(c in CODES for c in codes):
            plotted.append((owner, s, extra, codes))
        else:
            pending.append((owner, s))
    plotted.sort(key=lambda row: (list(CATEGORIES).index(row[1].get('category')) if row[1].get('category') in CATEGORIES else 3, row[2]))
    height = 62 + len(plotted) * 76
    axis_start, tick_gap = 500, 86
    parts = [svg_open(980, height, '类别与阶段码分布图，所有码均为推定；区间保留两个端点', 'stage-distribution')]
    for i, c in enumerate(CODES):
        x = axis_start + tick_gap*i
        parts.append(f'<line class="grid" x1="{x}" y1="42" x2="{x}" y2="{height-12}" fill="none" stroke="#dce3ec" stroke-width="1" stroke-dasharray="3 5"/>' + txt(x, 25, c, 'text-anchor="middle"', title=stage_meaning(c)))
    for i, (owner, s, extra, codes) in enumerate(plotted):
        y = 80 + 76*i
        cat = s.get('category', '')
        cls = CATEGORIES.get(cat, 'neutral')
        color = CATEGORY_COLORS[cls]
        detail = f'{owner} · {s.get("name", "未录入")} / {cat} / {s["code"]} / 推定'
        note = s.get('note', '')
        definitions = '；'.join(stage_meaning(c, cat if cat in CATEGORIES else None) for c in codes)
        if cat not in CATEGORIES:
            definitions = '按三问分别归类后参照：' + definitions
        parts.append(f'<g class="segment {cls}{" extra" if extra else ""}" data-code="{esc(s["code"])}" data-owner="{esc(owner)}"><title>{esc(detail + (" / " + note if note else "") + " / " + definitions)}</title>')
        parts.append(txt(14, y-10, f'{owner} · {s.get("name", "未录入")}', 'class="segment-name"'))
        parts.append(txt(14, y+12, f'{cat} · {s["code"]}（推定）', 'class="small"'))
        xs = [axis_start + tick_gap*CODES.index(c) for c in codes]
        if len(xs) == 2:
            parts.append(f'<line class="range" x1="{xs[0]}" y1="{y}" x2="{xs[1]}" y2="{y}" fill="none" stroke="{color}" stroke-width="4" stroke-linecap="round"/>')
        for x in xs:
            if extra:
                parts.append(f'<rect class="point extra-point" x="{x-8}" y="{y-8}" width="16" height="16" rx="3" fill="#fff" stroke="{color}" stroke-width="2.5" stroke-dasharray="4 2"/>')
            elif cls == 'neutral':
                parts.append(f'<path class="point neutral-point" d="M {x} {y-9} l 9 9 -9 9 -9 -9 Z" fill="#fff" stroke="{color}" stroke-width="2.5"/>')
            else:
                parts.append(f'<circle class="point" cx="{x}" cy="{y}" r="6" fill="{color}" stroke="{color}" stroke-width="2"/>')
        parts.append('</g>')
    parts.append('</svg>')
    undated = '<h3>尚未定码 / 观察细分</h3><ul class="pending">' + ''.join(f'<li>{esc(owner)} · {esc(s.get("name", "未录入"))} · {esc(s.get("category", "未录入"))} · {esc(s.get("code") or "未定码")}（{esc(s.get("evidence", "未录入"))}）{(" · " + esc(s["note"])) if s.get("note") else ""}</li>' for owner,s in pending) + '</ul>'
    if missing:
        undated += '<p class="empty stage-missing">阶段字段未录入：' + esc('、'.join(missing)) + '</p>'
    details = '<details><summary>查看全部细分、推定码与备注</summary><ul>' + ''.join(
        f'<li>{esc(owner)} · {esc(s.get("name", "未录入"))} / {esc(s.get("category", "未录入"))} / {esc(s.get("code") or "未定码")}（{esc(s.get("evidence", "未录入"))}）' + (' · ' + esc(s['note']) if s.get('note') else '') + '</li>'
        for owner, s, extra in segments) + '</ul></details>'
    return heading + legend + scroll(''.join(parts)) + undated + details


def bump_chart(m):
    hs, ts = m['horizons'], m['tracks']
    xs = [190 + 195*i for i in range(len(hs))]
    width, height = xs[-1] + 180, 110 + 52*len(ts)
    parts = [svg_open(width, height, '名次坡度图：名次来自矩阵的第 N；越靠上名次越前', 'bump-chart')]
    for x, h in zip(xs, hs):
        parts.append(txt(x, 28, h, 'text-anchor="middle"') + f'<line class="grid" x1="{x}" y1="50" x2="{x}" y2="{height-38}" fill="none" stroke="#dce3ec" stroke-width="1" stroke-dasharray="3 5"/>')
    omitted = []
    patterns = ['', '10 3', '3 3', '12 3 2 3', '7 5', '2 5', '15 4']
    for i, t in enumerate(ts):
        ranks = [rank(c, len(ts)) for c in cells_for(t, len(hs))]
        if any(n is None for n in ranks):
            omitted.append(t['name']); continue
        ys = [70 + 52*(n-1) for n in ranks]
        points = ' '.join(f'{x},{y}' for x,y in zip(xs,ys))
        parts.append(f'<g class="bump-track" data-track="{esc(t["slug"])}" tabindex="0"><title>{esc(t["name"])}：{esc(" → ".join(str(n) for n in ranks))}</title><polyline points="{points}" fill="none" stroke="#5d6b7d" stroke-width="2" opacity=".65" stroke-dasharray="{patterns[i % len(patterns)]}"/>')
        parts.append(txt(xs[0]-20, ys[0]+5, t['name'], 'text-anchor="end"'))
        parts.append(txt(xs[-1]+20, ys[-1]+5, t['name']))
        for x,y,n in zip(xs,ys,ranks):
            parts.append(f'<circle cx="{x}" cy="{y}" r="14" fill="#fff" stroke="#5d6b7d" stroke-width="1.5"/>' + txt(x, y+5, n, 'text-anchor="middle"'))
        parts.append('</g>')
    parts.append('</svg>')
    note = '未进入坡度图：' + '、'.join(omitted) + '（单元格数据异常或「第 N」解析失败；不补排名）。' if omitted else '七条赛道均由原始「第 N」解析；越靠上名次越前，线条不表示阶段码。'
    return '<h2>03 / 名次随时间维度的变化</h2><p class="note">' + esc(note) + '</p>' + scroll(''.join(parts))


def split_changelog(body):
    parts = re.split(r'\n## 变更记录\n', body, maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ''


def log_entries(body, title):
    return [(line[2:12], title, line[13:].strip()) for line in body.splitlines() if re.match(r'^- \d{4}-\d{2}-\d{2} ', line)]


def timeline(entries):
    parts = [svg_open(1040, 42 + len(entries)*94, '变更记录时间轴，日期和摘要均摘自原有变更条目；完整记录见下方列表', 'changelog-timeline')]
    parts.append(f'<line class="timeline-line" x1="150" y1="24" x2="150" y2="{max(24, len(entries)*94)}" fill="none" stroke="#dce3ec" stroke-width="2"/>')
    for i,(date,title,summary) in enumerate(entries):
        y = 35+i*94
        parts.append(f'<g><title>{esc(date + " · " + title + " · " + summary)}</title><circle class="timeline-dot" cx="150" cy="{y}" r="6" fill="#285cb8" stroke="#fff" stroke-width="3"/>' + txt(130,y+5,date,'text-anchor="end" class="small"') + txt(172,y+5,title,'class="timeline-title"'))
        excerpt = summary[:102] + ('…' if len(summary)>102 else '')
        for j,line in enumerate(textwrap.wrap(excerpt, width=52)):
            parts.append(txt(172,y+29+19*j,line,'class="small"'))
        parts.append('</g>')
    return '<h2>变更时间轴</h2><p class="note">按日期倒序；摘要摘录自已有条目，完整内容保留在变更列表。</p>' + scroll(''.join(parts)+'</svg>')


THEME_INIT = """<script>(function(){try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}})();</script>"""
THEME_BUTTON = """<script>(function(){var b=document.getElementById('theme-toggle');b.addEventListener('click',function(){var r=document.documentElement,t=r.dataset.theme||'system',n=t==='system'?'light':t==='light'?'dark':'system';if(n==='system')delete r.dataset.theme;else r.dataset.theme=n;try{if(n==='system')localStorage.removeItem('theme');else localStorage.setItem('theme',n);}catch(e){}label();});function label(){var t=document.documentElement.dataset.theme||'system';b.textContent='主题：'+({system:'跟随系统',light:'浅色',dark:'深色'})[t];}label();})();</script>"""


def card_sections(body):
    parts = re.split(r'(?=<h2(?:\s|>))', body)
    return ''.join('<section class="card">'+p+'</section>' for p in parts if p.strip())


def page(title, body, active, page_updated, tracks, updated):
    items = [('index.html', '总览')] + [(m['slug']+'.html', m['title']) for m,_ in tracks] + [('changelog.html','变更记录')]
    nav = ''.join(f'<a href="{esc(h)}"' + (' class="on" aria-current="page"' if t==active else '') + f'>{esc(t)}</a>' for h,t in items)
    built = datetime.date.today().isoformat()
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}｜{SITE_TITLE}</title>{THEME_INIT}<link rel="stylesheet" href="style.css?v={CSS_VERSION}"></head>
<body><a class="skip" href="#main">跳至正文</a><header><div class="header-inner"><div class="topbar"><a class="brand" href="index.html">{SITE_TITLE}</a><button id="theme-toggle" type="button" aria-label="切换浅色、深色或跟随系统主题">主题：跟随系统</button></div><nav aria-label="主导航">{nav}</nav></div></header>
<main id="main"><div class="page-heading"><p class="eyebrow">产业结构研究 / 七条赛道</p><h1>{esc(title)}</h1><p class="meta">本页更新：{esc(page_updated)} · 四个时间维度</p></div>{card_sections(body)}</main>
<footer>研究用途，非投资建议；不构成任何证券的买卖推荐。内容均来自公开资料与经脱敏的行业判断。<br>{DISCLAIMER}<br>更新日期：{esc(page_updated)}（全站最近更新 {esc(updated)}，生成于 {built}）</footer>{THEME_BUTTON}</body></html>'''


def selfcheck(directory, expected, matrix_valid=True):
    for name in expected:
        p = directory / name
        if not p.is_file() or not p.stat().st_size:
            raise BuildError(directory.name + '/' + name, 'MissingOutput')
        if 'noindex,nofollow' not in p.read_text(encoding='utf-8'):
            raise BuildError(directory.name + '/' + name, 'MissingNoindex')
    css = directory/'style.css'
    if not css.is_file() or not css.stat().st_size:
        raise BuildError(directory.name+'/style.css', 'MissingOutput')
    robots = directory/'robots.txt'
    if not robots.is_file() or 'Disallow: /' not in robots.read_text(encoding='utf-8'):
        raise BuildError(directory.name+'/robots.txt', 'InvalidRobots')
    if not (directory/'.nojekyll').is_file():
        raise BuildError(directory.name+'/.nojekyll', 'MissingOutput')
    if matrix_valid and 'class="matrix"' not in (directory/'index.html').read_text(encoding='utf-8'):
        raise BuildError(directory.name+'/index.html', 'MissingMatrix')


def publish(root):
    current, nxt, prev = (root/name for name in ('docs', 'docs.next', 'docs.prev'))
    if current.exists():
        remove(prev)
        current.rename(prev)
    try:
        nxt.rename(current)
    except Exception:
        if prev.exists():
            remove(current)
            prev.rename(current)
        raise BuildError('docs.next', 'ReplaceError') from None
    remove(prev)


def build(root):
    nxt, current, prev = (root/name for name in ('docs.next', 'docs', 'docs.prev'))
    try:
        if prev.exists() and not current.exists():
            prev.rename(current)
        remove(nxt)
        m = load_matrix(root)
        import markdown
        def md(body):
            rendered = markdown.markdown(body, extensions=['tables'])
            def table(match):
                def cell(match):
                    tag, attrs, text = match.groups()
                    visible = html.unescape(re.sub(r'<[^>]+>', '', text)).strip()
                    # Keep short labels intact; prose retains a readable wrapping width.
                    cls = 'compact-cell' if len(visible) <= 24 else 'prose-cell'
                    return f'<{tag}{attrs or ""} class="{cls}">{text}</{tag}>'
                marked = re.sub(r'<(th|td)(\s[^>]*)?>(.*?)</\1>', cell, match[0], flags=re.S)
                return scroll(marked.replace('<table>', '<table class="markdown-table">', 1))
            return re.sub(r'<table>.*?</table>', table, rendered, flags=re.S)
        pages = [read_page(p) for p in sorted((root/'content').glob('[0-9]*.md'))]
        by_slug = {meta['slug']: (meta,body) for meta,body in pages}
        if len(by_slug) != len(pages) or not all(s in by_slug for s in ('index','changelog-overview')):
            raise BuildError('content', 'MissingPages')
        tracks = [p for p in pages if p[0]['slug'] not in ('index','changelog-overview')]
        if any(t['slug'] not in by_slug for t in m['tracks']):
            raise BuildError('content/matrix.json', 'MissingTrackPage')
        updated = max(meta['updated'] for meta,_ in pages)
        nxt.mkdir()
        shutil.copyfile(root/'style.css', nxt/'style.css')
        (nxt/'robots.txt').write_text('User-agent: *\nDisallow: /\n', encoding='utf-8')
        (nxt/'.nojekyll').write_text('', encoding='utf-8')
        meta, body = by_slug['index']
        if body.count('<!--MATRIX-->') != 1:
            raise BuildError('content/00-overview.md', 'MissingMatrixMarker')
        visuals = matrix_html(m) + code_guide() + heatmap(m) + stage_chart(m) + bump_chart(m)
        rendered = md(body)
        rendered = rendered.replace('<p><!--MATRIX--></p>', visuals).replace('<!--MATRIX-->', visuals)
        (nxt/'index.html').write_text(page('总览', rendered, '总览', meta['updated'], tracks, updated), encoding='utf-8')
        entries = log_entries(by_slug['changelog-overview'][1], '总览')
        for meta, body in tracks:
            main, log = split_changelog(body)
            entries.extend(log_entries(log, meta['title']))
            rendered = track_guide_link(md(main)) + '<h2>变更记录</h2>' + md(log)
            (nxt/(meta['slug']+'.html')).write_text(page(meta['title'], rendered, meta['title'], meta['updated'], tracks, updated), encoding='utf-8')
        entries.sort(key=lambda row: row[0], reverse=True)
        full_log = '<h2>完整变更列表</h2><ul class="change-list">' + ''.join(f'<li><time datetime="{esc(d)}">{esc(d)}</time> <strong>{esc(t)}</strong>｜{esc(s)}</li>' for d,t,s in entries) + '</ul>'
        (nxt/'changelog.html').write_text(page('变更记录', timeline(entries)+full_log, '变更记录', updated, tracks, updated), encoding='utf-8')
        expected = ['index.html', 'changelog.html'] + [meta['slug']+'.html' for meta,_ in tracks]
        selfcheck(nxt, expected)
        publish(root)
        return len(expected)
    except Exception:
        remove(nxt)
        raise


def main():
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            count = build(ROOT)
    except BuildError as error:
        print(f'{error.path}: {error.kind}', file=sys.stderr)
        return 1
    except Exception:
        print('build.py: BuildError', file=sys.stderr)
        return 1
    print(f'PASS: {count} pages -> docs/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
