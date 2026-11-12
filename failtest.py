#!/usr/bin/env python3
"""Exercise disposable copies and preserve the successful local preview."""
import ast
import contextlib
import hashlib
import io
import json
import pathlib
import re
import runpy
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent
WORK = ROOT / '_failtest_work'


def digest_tree(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob('*')) if p.is_file()}


def require(condition):
    if not condition:
        raise AssertionError


def fresh(name):
    case = WORK / name
    case.mkdir()
    for filename in ('build.py', 'style.css'):
        shutil.copyfile(ROOT/filename, case/filename)
    (case/'content').mkdir()
    for path in (ROOT/'content').glob('[0-9]*.md'):
        shutil.copyfile(path, case/'content'/path.name)
    shutil.copyfile(ROOT/'content/matrix.json', case/'content/matrix.json')
    shutil.copytree(ROOT/'docs', case/'docs')
    return case


def execute(case, script='build.py'):
    result = subprocess.run([sys.executable, script], cwd=case, capture_output=True, text=True, timeout=40)
    output = result.stdout + result.stderr
    require(str(ROOT) not in output and 'Traceback' not in output and 'untrusted' not in output)
    require(not (case/'docs.next').exists())
    return result


def write_matrix(case, m):
    (case/'content/matrix.json').write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')


def check_matrix_failures():
    modes = ('missing', 'empty', 'invalid', 'missing_horizons', 'missing_tracks')
    kinds = ('MissingFile', 'EmptyFile', 'InvalidJSON', 'MissingFields', 'MissingFields')
    for mode, kind in zip(modes,kinds):
        case = fresh(mode)
        before = digest_tree(case/'docs')
        matrix = case/'content/matrix.json'
        m = json.loads(matrix.read_text())
        if mode == 'missing':
            matrix.unlink()
        elif mode == 'empty':
            matrix.write_text('')
        elif mode == 'invalid':
            matrix.write_text('{invalid')
        else:
            del m[mode.removeprefix('missing_')]
            write_matrix(case, m)
        result = execute(case)
        require(result.returncode != 0)
        require(result.stdout == '' and result.stderr == f'content/matrix.json: {kind}\n')
        require(before == digest_tree(case/'docs'))
    return '缺失、空文件、无效 JSON、缺 horizons、缺 tracks 均非 0；docs/ 哈希不变，错误仅相对路径与类型'


def check_missing_stages():
    for field in ('all', 'category', 'stage_display', 'stage_segments'):
        case = fresh('stage_'+field)
        m = json.loads((case/'content/matrix.json').read_text())
        for t in m['tracks']:
            for key in ('category', 'stage_display', 'stage_segments') if field == 'all' else (field,):
                t.pop(key)
        write_matrix(case, m)
        result = execute(case)
        require(result.returncode == 0)
        page = (case/'docs/index.html').read_text()
        require(page.count('<td class="stage-cell">未录入</td>') == 7)
        require('阶段字段未录入' in page and 'class="chart stage-distribution"' not in page)
        require('class="chart heatmap"' in page and 'class="chart bump-chart"' in page)
        require(page.count('class="bump-track"') == 7)
    return '全部或任一阶段字段缺失仍成功；类别标未录入，分布图无假点，热力图与七条坡度线保留'


def check_bad_cells():
    case = fresh('bad_cells')
    m = json.loads((case/'content/matrix.json').read_text())
    m['tracks'][0].pop('cells')
    m['tracks'][1]['cells'].pop()
    m['tracks'][2]['cells'][0][0] = '无效标签'
    m['tracks'][3]['cells'][0][1] = '名次缺失｜保留判断'
    write_matrix(case, m)
    result = execute(case)
    require(result.returncode == 0)
    page = (case/'docs/index.html').read_text()
    table = re.search(r'<table class="matrix">.*?</table>', page, re.S)[0]
    require(table.count('<td class="invalid">数据异常</td>') == 9)
    rows = re.findall(r'<tr>.*?</tr>', table, re.S)[1:]
    require(rows[0].count('数据异常') == 4 and rows[1].count('数据异常') == 4)
    require(rows[2].count('数据异常') == 1)
    require('名次缺失｜保留判断' in rows[3])
    require(page.count('class="bump-track"') == 3)
    for i,t in enumerate(m['tracks']):
        require((f'data-track="{t["slug"]}"' in page) == (i >= 4))
    require('未进入坡度图：半导体、商业航天、人形机器人、高端装备' in page)
    invalid_cells = re.findall(r'<g class="heat invalid">.*?</g>', page, re.S)
    require(len(invalid_cells) == 9)
    for source in invalid_cells:
        group = ET.fromstring(source)
        rect = group.find('rect')
        require(rect.get('fill') == '#e9edf2' and rect.get('stroke') == '#606974')
        require(all(t.get('fill') == '#606974' for t in group.findall('text')))
    for t in m['tracks'][4:]:
        for c in t['cells']:
            import html
            require(html.escape(c[1]) in table)
    return '缺 cells、格数错误、非法标签标数据异常；其他行保持；解析失败的四条赛道从坡度图排除并具名说明'


PROBE = '''import pathlib
import build
root = pathlib.Path(__file__).resolve().parent
mode = MODE
if mode in ('rename', 'partial'):
    original = pathlib.Path.rename
    def rename(self, target):
        if self == root/'docs.next':
            if mode == 'partial':
                (root/'docs').mkdir()
                (root/'docs/index.html').write_text('incomplete')
            raise OSError('untrusted diagnostic')
        return original(self, target)
    pathlib.Path.rename = rename
else:
    original = build.selfcheck
    def selfcheck(directory, expected, matrix_valid=True):
        if mode == 'html':
            (directory/'ai.html').unlink()
        elif mode == 'empty_html':
            (directory/'ai.html').write_text('')
        elif mode == 'css':
            (directory/'style.css').write_text('')
        elif mode == 'robots':
            (directory/'robots.txt').write_text('User-agent: *')
        elif mode == 'nojekyll':
            (directory/'.nojekyll').unlink()
        elif mode == 'noindex':
            p=directory/'ai.html'
            p.write_text(p.read_text().replace('noindex,nofollow', ''))
        elif mode == 'matrix':
            p=directory/'index.html'
            p.write_text(p.read_text().replace('class="matrix"', 'class="removed"'))
        return original(directory, expected, matrix_valid)
    build.selfcheck = selfcheck
raise SystemExit(build.main())
'''


def check_atomicity():
    for mode in ('html','empty_html','css','robots','nojekyll','noindex','matrix','rename','partial'):
        case = fresh('atomic_'+mode)
        before = digest_tree(case/'docs')
        (case/'docs.prev').mkdir()
        (case/'docs.prev/stale.txt').write_text('stale')
        (case/'atomic_probe.py').write_text(PROBE.replace('MODE', repr(mode)))
        result = execute(case, 'atomic_probe.py')
        require(result.returncode != 0)
        require(before == digest_tree(case/'docs'))
        require(re.fullmatch(r'docs\.next(?:/[a-z.]+)?: [A-Za-z]+\n', result.stderr) is not None)
        if mode in ('rename','partial'):
            require(not (case/'docs.prev').exists())
    case = fresh('stale_success')
    (case/'docs.prev').mkdir()
    (case/'docs.prev/stale.txt').write_text('stale')
    require(execute(case).returncode == 0)
    require(not (case/'docs.prev').exists())
    return '七种自检失败均保留旧 docs/；第二次 rename 失败及出现不完整 docs/ 时回滚成功；成功构建清理残留'


def check_output():
    docs = ROOT/'docs'
    pages = list(docs.glob('*.html'))
    require(len(pages) == 9)
    for p in pages:
        s=p.read_text()
        require(re.findall(r'<link rel="stylesheet" href="([^"]+)"', s) == ['style.css?v=20261112-legend1'])
        require('<meta name="robots" content="noindex,nofollow">' in s)
        require('阶段码为产业结构研究口径，非投资建议，不反映任何持仓。' in s)
        require(not re.search(r'<script[^>]*\bsrc=',s))
    require((docs/'robots.txt').read_text() == 'User-agent: *\nDisallow: /\n')
    require((docs/'.nojekyll').is_file())
    page=(docs/'index.html').read_text()
    m=json.loads((ROOT/'content/matrix.json').read_text())
    svgs={}
    for source in re.findall(r'<svg\b.*?</svg>', page, re.S):
        element=ET.fromstring(source)
        if element.attrib.get('class') == 'category-marker':
            require(element.attrib.get('aria-hidden') == 'true')
            continue
        require(element.attrib.get('role')=='img' and element.attrib.get('aria-label'))
        svgs[element.attrib['class'].split()[-1]]=element
    require(set(svgs)=={'heatmap','stage-distribution','bump-chart'})
    ns={'s':'http://www.w3.org/2000/svg'}
    chart=svgs['stage-distribution']
    groups=chart.findall('s:g',ns)
    expected=[s for t in m['tracks'] for s in t['stage_segments']] + m['extra_segments']
    coded=[s for s in expected if s['code'] not in ('尚未定码','观察','')]
    require(len(groups)==len(coded)==22)
    ranges=0
    for g in groups:
        code=g.attrib['data-code']
        endpoints=[e for e in g if 'point' in e.attrib.get('class','').split()]
        title=g.find('s:title',ns).text
        require('推定' in title)
        if '–' in code or '→' in code:
            ranges+=1
            require(len(endpoints)==2 and len(g.findall('s:line',ns))==1)
        else:
            require(len(endpoints)==1 and not g.findall('s:line',ns))
        if 'neutral' in g.attrib['class']:
            require(all(e.tag.endswith('path') for e in endpoints))
        if 'extra' in g.attrib['class']:
            require(all('extra-point' in e.attrib['class'] for e in endpoints))
    require(ranges==6)
    require('尚未定码 / 观察细分' in page and '脑机接口' in page)
    require(page.count('class="bump-track"') == 7)
    log=(docs/'changelog.html').read_text()
    timeline=ET.fromstring(re.search(r'<svg\b.*?</svg>',log,re.S)[0])
    require(timeline.attrib['class']=='chart changelog-timeline')
    expected_logs=0
    for p in (ROOT/'content').glob('[0-9]*.md'):
        body=p.read_text()
        if p.name=='90-changelog-overview.md':
            source=body
        elif '## 变更记录\n' in body:
            source=body.split('## 变更记录\n',1)[1]
        else:
            continue
        expected_logs+=len(re.findall(r'^- \d{4}-\d{2}-\d{2} ',source,re.M))
    require(len(timeline.findall('s:g',ns))==expected_logs)
    require(len(re.findall('<time datetime=',log))==expected_logs)
    return '9 页 noindex、robots、页脚与四图通过；22 个定码细分含 6 个双端点区间；时间轴条目与原始记录逐数一致'


def check_code_guide():
    import html
    forbidden_stage_name = '\u7acb\u9879'
    source = (ROOT/'build.py').read_text()
    tree = ast.parse(source)
    definition_nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'STAGE_DEFINITIONS'
                                for target in node.targets)]
    meaning_nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('stage_meaning', 'code_guide')]
    require(len(definition_nodes) == 1 and {node.name for node in meaning_nodes} == {'stage_meaning', 'code_guide'})
    for node in definition_nodes + meaning_nodes:
        require(forbidden_stage_name not in ast.get_source_segment(source, node))
        require(all(forbidden_stage_name not in child.value for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)))
    build = runpy.run_path(str(ROOT/'build.py'))
    require(forbidden_stage_name not in json.dumps(build['STAGE_DEFINITIONS'], ensure_ascii=False))
    require(forbidden_stage_name not in html.unescape(build['code_guide']()))
    for code in build['CODES']:
        for category in (None, *build['CATEGORIES']):
            require(forbidden_stage_name not in build['stage_meaning'](code, category))
    page = (ROOT/'docs/index.html').read_text()
    guide = re.search(r'<section class="card"><h2>编码说明</h2>(.*?)</section>', page, re.S)[1]
    require(forbidden_stage_name not in html.unescape(guide))
    require(guide.count('id="code-guide"') == page.count('id="code-guide"') == 1)
    require(re.search(r'<details\b[^>]*\bopen(?:\s|>)', guide))
    require('<summary>怎么读类别、阶段码和颜色</summary>' in guide)
    visible = html.unescape(re.sub(r'<[^>]+>', '', guide))
    normalized = re.sub(r'\s+', '', visible)
    for phrase in ('三类共用同一套阶段码', '按顺序，命中即停', '落档依据只有二手来源',
                   '还没有给出阶段码', '颜色表示判断，不是阶段', '线条只表示名次随时间维度的变化'):
        require(phrase in normalized)
    require(page.index('class="matrix"') < page.index('<h2>编码说明</h2>') < page.index('<h2>01 / 相对吸引力热力图</h2>'))
    require('<a href="#code-guide">编码说明</a>' in re.search(r'<table class="matrix">.*?</table>', page, re.S)[0])
    table = ET.fromstring(re.search(r'<table class="code-table">.*?</table>', guide, re.S)[0])
    rows = [list(row) for row in table.findall('./tbody/tr')]
    codes = ['S0', 'S1a', 'S1b', 'S2', 'S3a', 'S3b']
    require(len(rows) == 6 and all(len(row) == 5 for row in rows))
    require([row[0].text for row in rows] == codes)
    definitions = {row[0].text: [cell.text for cell in row[2:]] for row in rows}
    chart = ET.fromstring(re.search(r'<svg[^>]*class="chart stage-distribution".*?</svg>', page, re.S)[0])
    ns = {'s': 'http://www.w3.org/2000/svg'}
    axes = chart.findall('s:text', ns)
    require(len(axes) == 6)
    for axis, code, level in zip(axes, codes, ('始局', '始局', '始局', '现局', '终局', '终局')):
        title = axis.find('s:title', ns)
        require(title is not None and title.tail == code and level in title.text and '区间' not in title.text)
        require(forbidden_stage_name not in title.text)
        require(all(definition in title.text for definition in definitions[code]))
    m = json.loads((ROOT/'content/matrix.json').read_text())
    segments = [(t['name'], s) for t in m['tracks'] for s in t['stage_segments']]
    segments += [('赛道以外', s) for s in m['extra_segments']]
    groups = chart.findall('s:g', ns)
    require(len(groups) == 22 and all(g.get('data-code') for g in groups))
    for group in groups:
        name = group.find('s:text', ns).text
        matches = [s for owner, s in segments if name == owner + ' · ' + s['name']]
        require(len(matches) == 1)
        segment = matches[0]
        title = group.find('s:title', ns).text
        require('推定' in title and segment['code'] == group.get('data-code'))
        # Check only the added meanings, preserving original names and notes.
        prefix = f'{name} / {segment["category"]} / {segment["code"]} / 推定'
        if segment.get('note'):
            prefix += ' / ' + segment['note']
        prefix += ' / '
        require(title.startswith(prefix))
        require(forbidden_stage_name not in title[len(prefix):])
        categories = ['国产替代', '自主可控', '未来产业']
        indices = [categories.index(segment['category'])] if segment['category'] in categories else range(3)
        for code in re.split('[–→]', segment['code']):
            require(all(definitions[code][i] in title for i in indices))
    for track in m['tracks']:
        rendered = (ROOT/'docs'/(track['slug']+'.html')).read_text()
        link = '<a href="index.html#code-guide">编码说明</a>'
        require(rendered.count(link) == 1)
        require(link in re.search(r'<p><strong>类别与阶段.*?</p>', rendered, re.S)[0])
    require('href="index.html#code-guide"' not in (ROOT/'docs/changelog.html').read_text())
    case = fresh('guide_repeat')
    before = digest_tree(case/'docs')
    require(execute(case).returncode == 0 and digest_tree(case/'docs') == before)
    require(execute(case).returncode == 0 and digest_tree(case/'docs') == before)
    (case/'guide_probe.py').write_text('import build\ns = \'<p><strong>类别与阶段（推定）：</strong> S1a</p>\'\na = build.track_guide_link(s)\nassert a == build.track_guide_link(a)\n')
    require(execute(case, 'guide_probe.py').returncode == 0)
    return '说明卡位置、展开与原句；七页锚点；六轴及 22 组定义；阶段短名检查仅限说明卡、定义源码和含义悬停；双端点与重复构建幂等均通过'


def check_svg_colors():
    ns = {'s': 'http://www.w3.org/2000/svg'}
    def hex_color(value):
        return re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?', value or '') is not None

    class Badges(HTMLParser):
        def __init__(self):
            super().__init__()
            self.categories = self.judgments = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            classes = attrs.get('class', '').split()
            if tag == 'span' and ('category' in classes or 'tag' in classes):
                style = dict(part.split(':', 1) for part in attrs['style'].split(';') if part)
                require(hex_color(style.get('color')))
                require('margin' in style or 'margin-right' in style)
                if 'category' in classes:
                    self.categories += 1
                    require(style.get('display') == 'inline-flex' and style.get('gap') == '6px')
                else:
                    self.judgments += 1
                    require(hex_color(style.get('background')))

    charts = {}
    marker_count = 0
    for name in ('index.html', 'changelog.html'):
        page = (ROOT/'docs'/name).read_text()
        require(not re.search(r'<i\b', page))
        for source in re.findall(r'<svg\b.*?</svg>', page, re.S):
            svg = ET.fromstring(source)
            cls = svg.attrib['class']
            require(svg.attrib['viewBox'].split()[2:] == [svg.attrib['width'], svg.attrib['height']])
            if cls == 'category-marker':
                marker_count += 1
            else:
                charts[cls.split()[-1]] = svg
            for el in svg.iter():
                tag = el.tag.rsplit('}', 1)[-1]
                if tag == 'text':
                    require(hex_color(el.get('fill')))
                elif tag in ('line', 'polyline', 'circle', 'rect', 'path'):
                    require(hex_color(el.get('stroke')) and float(el.get('stroke-width', '0')) > 0)
                    require(el.get('fill') == 'none' if tag in ('line', 'polyline') else hex_color(el.get('fill')))
        if name == 'index.html':
            badges = Badges()
            badges.feed(page)
            require(badges.categories >= 4 and badges.judgments >= 3)
            require(marker_count == badges.categories)
    require(set(charts) == {'heatmap', 'stage-distribution', 'bump-chart', 'changelog-timeline'})
    judgments = {'go': ('#8c4b12', '#fff0ce'), 'watch': ('#685913', '#f5f1d9'), 'no': ('#606974', '#e9edf2')}
    for group in charts['heatmap'].findall('s:g', ns):
        fg, bg = judgments[group.get('class').split()[-1]]
        rect = group.find('s:rect', ns)
        require(rect.get('fill') == bg and rect.get('stroke') == fg)
        require(all(t.get('fill') == fg for t in group.findall('s:text', ns)))
    categories = {'domestic': '#245fc4', 'sovereign': '#087d73', 'future': '#7846ba', 'neutral': '#606a7c'}
    for group in charts['stage-distribution'].findall('s:g', ns):
        color = categories[group.get('class').split()[1]]
        for el in group:
            if 'point' in el.get('class', '').split():
                require(el.get('stroke') == color)
                require(el.get('fill') == (color if el.tag.endswith('circle') else '#fff'))
            elif el.tag.endswith('line'):
                require(el.get('fill') == 'none' and el.get('stroke') == color)
    for line in charts['bump-chart'].findall('.//s:polyline', ns):
        require(line.get('fill') == 'none' and line.get('stroke') == '#5d6b7d')
    for circle in charts['bump-chart'].findall('.//s:circle', ns):
        require(circle.get('fill') == '#fff' and circle.get('stroke') == '#5d6b7d')
    for circle in charts['changelog-timeline'].findall('.//s:circle', ns):
        require(circle.get('fill') == '#285cb8' and circle.get('stroke') == '#fff')
    css = (ROOT/'style.css').read_text()
    require(css == (ROOT/'docs/style.css').read_text())
    require(not re.search(r'(?:fill|stroke)\s*:\s*var\(', css))
    require('color-mix' not in css and 'oklch' not in css)
    for prefix in (':root:not([data-theme="light"])', ':root[data-theme="dark"]'):
        require(prefix + ' .chart text{fill:#e2e9f4}' in css)
        require(prefix + ' .bump-track polyline{fill:none;stroke:#a5b5ca}' in css)
        # Match the category rule's specificity so hollow points stay hollow.
        hollow_selectors = ['.segment.neutral .neutral-point']
        hollow_selectors += [f'.segment.{cls} .extra-point' for cls in categories]
        hollow_selectors += ['.category.neutral .category-marker path']
        hollow_rule = ', '.join(prefix + ' ' + selector for selector in hollow_selectors) + '{fill:#192332}'
        require(hollow_rule in css)
        require(css.index(hollow_rule) > css.index(prefix + ' .segment.neutral .point'))
        for cls, fg, bg in (('go', '#ffcc80', '#4d351d'), ('watch', '#e2d697', '#39351f'), ('no', '#b9c3d1', '#2c3645')):
            require(prefix + f' .heat.{cls} rect{{fill:{bg};stroke:{fg}}}' in css)
            require(prefix + f' .heat.{cls} text{{fill:{fg}}}' in css)
            require(prefix + f' .tag.{cls}{{color:{fg}!important;background:{bg}!important}}' in css)
        for cls, color in (('domestic', '#89b5ff'), ('sovereign', '#68d8c4'), ('future', '#c6a0ff')):
            require(prefix + f' .category.{cls}' in css and f'fill:{color};stroke:{color}' in css)
    forbidden = ('\u5f85\u8865', '\u5f85\u6838')
    files = [ROOT/'build.py', ROOT/'failtest.py', ROOT/'style.css']
    files += list((ROOT/'content').glob('*.md')) + [ROOT/'content/matrix.json']
    files += [p for p in (ROOT/'docs').iterdir() if p.suffix in ('.html', '.css', '.txt')]
    for path in files:
        require(all(word not in path.read_text() for word in forbidden))
    return '四图均有明确 SVG 颜色与尺寸；折线无填充；图例含颜色与间距；双深色覆盖、CSS 版本和源码/产物检查通过'


def main():
    all_ok=True
    before_docs=digest_tree(ROOT/'docs')
    before_matrix=(ROOT/'content/matrix.json').read_bytes()
    before_content=digest_tree(ROOT/'content')
    try:
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir()
        checks=[('场景 1',check_matrix_failures),('场景 2',check_missing_stages),('场景 3',check_bad_cells),('原子性与回滚',check_atomicity),('产物抽查',check_output),('编码说明与悬停',check_code_guide),('SVG 颜色与缓存',check_svg_colors)]
        for name,check in checks:
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    detail=check()
                print(f'{name}: PASS — {detail}')
            except Exception:
                all_ok=False
                print(f'{name}: FAIL — failtest.py: CheckError')
    finally:
        if WORK.exists():
            shutil.rmtree(WORK)
    preserved=(before_docs==digest_tree(ROOT/'docs') and before_matrix==(ROOT/'content/matrix.json').read_bytes()
               and before_content==digest_tree(ROOT/'content'))
    all_ok=all_ok and preserved
    print('隔离与清理: '+('PASS' if preserved and not WORK.exists() else 'FAIL')+' — docs/ 与 content/matrix.json 未改动；_failtest_work/ 已清理')
    return 0 if all_ok else 1


if __name__=='__main__':
    try:
        status=main()
    except Exception:
        print('failtest.py: TestError',file=sys.stderr)
        status=1
    sys.exit(status)
