# 硬科技一级市场趋势观察（公开脱敏版）

- `content/`：脱敏后的 Markdown（总览、六条赛道、总览变更）和 `matrix.json`（6 赛道 × 4 时间维度矩阵）。
- `build.py`：`python3 build.py` 生成 `docs/`（GitHub Pages 从 main 分支 /docs 发布；每页带 noindex,nofollow，另有 robots.txt）（纯静态 HTML/CSS，可直接用于 GitHub Pages）。
- `leak_scan.py`：发布前必须运行，结果必须为 0。它读取仓库外的保密名单，名单本身不进仓库。
- `leak_allowlist.txt`：人工复核过的误报（上市公司公开数据等）。

**禁止提交未脱敏材料。** 原始导出在仓库外，不得复制进本目录或提交。
