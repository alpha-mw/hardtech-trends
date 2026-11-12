#!/usr/bin/env python3
"""Exercise disposable copies and preserve the successful local preview."""
import contextlib
import hashlib
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

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


def main():
    all_ok=True
    before_docs=digest_tree(ROOT/'docs')
    before_matrix=(ROOT/'content/matrix.json').read_bytes()
    try:
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir()
        checks=[('场景 1',check_matrix_failures),('场景 2',check_missing_stages),('场景 3',check_bad_cells),('原子性与回滚',check_atomicity),('产物抽查',check_output)]
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
    preserved=(before_docs==digest_tree(ROOT/'docs') and before_matrix==(ROOT/'content/matrix.json').read_bytes())
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
