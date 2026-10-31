# SecPaper-DeepScan

从 arXiv 抓取论文、按关键词去重筛选，并用模型对摘要做结构化分析。

## 标准使用流程

克隆仓库后，在项目根目录按下面做一遍即可。

**1. 环境**

需要本机已安装 Python 3.10+。

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
```

`setup.ps1` 会创建 `.venv`、安装依赖，并询问 API 配置后写入 `.env`。

**2. API 配置**

三项写在根目录 `.env`（由 `setup.ps1` 生成，也可自己建）：

- `AI_MODEL`
- `AI_BASE_URL`
- `AI_API_KEY`

**3. 提示词配置**

直接改这两个文件：

- `prompts/system.txt`
- `prompts/user.txt`

`user.txt` 须保留占位符 `{{INPUT_DATA}}`。

**4. 拉取**

```powershell
python src/fetcher_add_summary.py --category cs.AI --year 2026 --month 4 --per-request 500
```

结果在 `results/`，例如 `results/cs.AI_2026_04_4717_summary.json`。

**5. 去重 / 关键词筛选（可选）**

```powershell
# 单文件按 keywords.json 筛选 → results_temp01/
python src/deduplicate_results.py cs.AI_2026_04_4717_summary.json --keyword-groups-file keywords.json

# 多文件：第一个减去后面已出现的 arXiv ID → results_temp02/
# 若同时给关键词，最终结果在 results_temp03/
python src/deduplicate_results.py cs.AI_2026_04_4717_summary.json cs.CR_2026_04_852_summary.json --keyword-groups-file keywords.json
```

**6. 分析**

输入必须带非空 `summary`。

```powershell
python src/analyzer_no_summary.py --input results/cs.AI_2026_04_4717_summary.json
python src/analyzer_no_summary.py --input results_temp03/cs.AI-CR_2026_04_759.json --range 1-200
```

分析结果写到当前目录，文件名形如 `analysis_cs.AI_2026_04.json`。

---

## `src/fetcher_add_summary.py`

按分类和年月从 arXiv 抓论文（含摘要），写入 `results/{category}_{year}_{month}_{count}_summary.json`。

```powershell
python src/fetcher_add_summary.py --category cs.CR --year 2026 --month 1 --per-request 500
```

| 参数 | 说明 |
| --- | --- |
| `--category` | arXiv 分类，默认 `cs.CR` |
| `--year` / `--month` | 必需 |
| `--per-request` | 每次请求条数，默认 `100` |
| `--pause` | 请求间隔秒，默认 `3` |

---

## `src/analyzer_no_summary.py`

用输入 JSON 里已有的 `summary` 调模型分析，不下载 PDF、不拉 HTML。缺 `summary` 的条目 `analysis.error` 为 `no_summary`。配置来自 `.env`，提示词来自 `prompts/`。

```powershell
python src/analyzer_no_summary.py --input results/cs.AI_2026_04_4717_summary.json --limit 5
```

| 参数 | 说明 |
| --- | --- |
| `--input` | 带 `summary` 的论文列表 JSON |
| `--limit` | 只分析前 N 篇 |
| `--range` | 下标区间，如 `1-50` |
| `--model` / `--base-url` / `--api-key` | 覆盖 `.env` |

输出名由输入文件名推导：`analysis_{分类}_{年}_{月}[_范围].json`。

---

## `src/deduplicate_results.py`

读取 `results/` 下 JSON。按 arXiv 核心 ID 去重（忽略版本号）。可选在 `title + summary` 里做关键词匹配（优先用已有 `summary`，没有才补抓）。输入须同年同月。

```powershell
python src/deduplicate_results.py cs.CR_2026_07_838_summary.json --keyword-groups-file keywords.json
python src/deduplicate_results.py cs.AI_2026_04_4717_summary.json cs.CR_2026_04_852_summary.json
```

| 参数 | 说明 |
| --- | --- |
| `inputs` | `results/` 下的一个或多个 JSON；多文件时第一个减去后面的并集 |
| `--keyword-groups-file` | 关键词 JSON 列表文件 |
| `--keyword-groups` | 直接传关键词 JSON 字符串 |
| `--pause` | 补抓摘要的间隔秒，默认 `3` |

输出：单文件筛选 → `results_temp01/`；多文件去重 → `results_temp02/`；去重后再筛选 → `results_temp03/`。
