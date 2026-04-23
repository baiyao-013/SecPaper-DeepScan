# SecPaper-DeepScan

用于抓取 arXiv `cs.CR` 最近论文并做漏洞相关方法分析。

## 1. Fetcher 使用方法

脚本位置: `src/fetcher.py`

功能:
- 查询 arXiv 中 `cs.CR` 分类、最近 N 个月提交的论文。
- 输出每篇论文的基础信息: `title`、`published_time`、`authors`、`pdf_link`、`html_link`、`conference_metadata`。

默认命令:

```bash
python src/fetcher.py
```

常用命令:

```bash
# 输出到指定文件
python src/fetcher.py --output cs_cr_last_2_months.json

# 查询最近 3 个月
python src/fetcher.py --months 3

# 降低请求次数，减少限流风险
python src/fetcher.py --per-request 2000
```

参数说明:
- `--output`: 输出 JSON 文件路径，默认 `cs_cr_last_2_months.json`
- `--months`: 回溯月数，默认 `2`
- `--per-request`: 每次请求的返回数量，默认 `100`

进度条:
- 若安装了 `tqdm` 库，会显示批次抓取进度条、已处理数量和预计剩余时间

## 2. Analyzer 使用方法

脚本位置: `src/analyzer.py`

功能:
- 读取 fetcher 的 JSON 列表。
- 下载论文文件: 优先 PDF；PDF 下载失败时回退 HTML（arXiv abs 页面）。
- 调用 AI 对每篇论文做结构化分析，判断:
	- 是否涉及漏洞发现/漏洞利用/漏洞防御
	- 使用的方法、是否创新、相对传统方法的创新点
	- 针对的漏洞类型

配置方式:
- 当前版本在脚本内写死 AI 配置常量:
	- `AI_MODEL`
	- `AI_BASE_URL`
	- `AI_API_KEY`

运行命令:

```bash
# 全量分析
python src/analyzer.py --input cs_cr_last_2_months.json --output analysis_results.json --download-dir downloads

# 小样本测试（先跑 10 篇）
python src/analyzer.py --input cs_cr_last_2_months.json --output analysis_results_sample.json --download-dir downloads_sample --limit 10 --sleep 0
```

参数说明:
- `--input`: 输入论文列表 JSON，默认 `cs_cr_last_2_months.json`
- `--output`: 输出分析结果 JSON，默认 `analysis_results.json`
- `--download-dir`: 下载文件目录，默认 `downloads`
- `--limit`: 仅分析前 N 篇（调试用）
- `--sleep`: 每篇之间的等待秒数，默认 `0.2`

进度条:
- 若安装了 `tqdm` 库，会显示论文分析的进度条和完成百分比

## 3. 推荐流程

```bash
# Step 1: 抓取论文列表
python src/fetcher.py --output cs_cr_last_2_months.json --per-request 2000

# Step 2: 分析论文
python src/analyzer.py --input cs_cr_last_2_months.json --output analysis_results.json --download-dir downloads
```

## 4. 输出文件说明

- 论文列表: `cs_cr_last_2_months.json`
- 分析结果: `analysis_results.json`
- 下载目录: `downloads/`