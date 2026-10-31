#!/usr/bin/env python3
"""
分析器（无摘要拉取）：直接使用输入 JSON 中的 summary 字段调用模型分析，不从 arXiv HTML 获取摘要。

面向 world model 论文：区分具身/非具身、攻击/防御，并只从摘要抽取威胁模型信息（缺失则留空，禁止编造）。

输入 JSON 每条需包含非空 summary；缺少 summary 的条目会写入 analysis.error = "no_summary"。
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from config import ROOT, get_ai_config

try:
	from tqdm import tqdm
except ImportError:
	def tqdm(iterable, **kwargs):
		return iterable


USER_AGENT = "SecPaper-DeepScan/1.1 (no-summary-fetch analyzer)"
PROMPT_DIR = ROOT / "prompts"
INPUT_PLACEHOLDER = "{{INPUT_DATA}}"


@lru_cache(maxsize=None)
def load_prompt_template(name: str) -> str:
	path = PROMPT_DIR / f"{name}.txt"
	if not path.is_file():
		raise FileNotFoundError(f"Missing prompt template: {path}")
	return path.read_text(encoding="utf-8")


def safe_filename(value: str, max_len: int = 120) -> str:
	cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_.")
	if not cleaned:
		cleaned = "paper"
	return cleaned[:max_len]


def input_summary(item: dict[str, Any]) -> str | None:
	summary = item.get("summary")
	if not isinstance(summary, str):
		return None
	text = summary.strip()
	return text or None


def build_analysis_prompt(item: dict[str, Any], document_text: str | None) -> str:
	context = {
		"title": item.get("title"),
		"published_time": item.get("published_time"),
		"authors": item.get("authors"),
		"conference_metadata": item.get("conference_metadata"),
		"paper_content": document_text,
	}
	template = load_prompt_template("user")
	if INPUT_PLACEHOLDER not in template:
		raise ValueError(f"Prompt template must contain {INPUT_PLACEHOLDER}")
	return template.replace(INPUT_PLACEHOLDER, json.dumps(context, ensure_ascii=False))


def call_openai_compatible(prompt: str, model: str, api_key: str, base_url: str) -> dict[str, Any]:
	url = base_url.rstrip("/") + "/chat/completions"
	payload = {
		"model": model,
		"temperature": 0.1,
		"response_format": {"type": "json_object"},
		"messages": [
			{
				"role": "system",
				"content": load_prompt_template("system").strip(),
			},
			{
				"role": "user",
				"content": prompt,
			},
		],
	}
	body = json.dumps(payload).encode("utf-8")
	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
		"User-Agent": USER_AGENT,
	}
	req = urllib.request.Request(url, data=body, headers=headers, method="POST")
	with urllib.request.urlopen(req, timeout=90) as resp:
		result = json.loads(resp.read().decode("utf-8"))

	content = result["choices"][0]["message"]["content"]
	if isinstance(content, list):
		content = "".join(block.get("text", "") for block in content if isinstance(block, dict))

	return json.loads(content)


def analyze_item(item: dict[str, Any], summary: str | None) -> dict[str, Any]:
	if summary is None:
		return {"error": "no_summary"}

	model, base_url, api_key = get_ai_config()

	if api_key:
		prompt = build_analysis_prompt(item, summary)
		try:
			return call_openai_compatible(prompt, model=model, api_key=api_key, base_url=base_url)
		except Exception as exc:  # noqa: BLE001
			return {"error": f"analysis failed: {type(exc).__name__}"}

	return {"error": "no API key available"}


def load_input_items(input_json: Path) -> list[dict[str, Any]]:
	data = json.loads(input_json.read_text(encoding="utf-8"))
	if isinstance(data, list):
		return data
	if isinstance(data, dict) and isinstance(data.get("papers"), list):
		return data["papers"]
	raise ValueError("Input JSON must be a list or an object with a 'papers' list")


def process_item(item: dict[str, Any]) -> dict[str, Any] | None:
	if not isinstance(item, dict):
		return None

	summary = input_summary(item)
	analysis = analyze_item(item, summary)

	result_item: dict[str, Any] = {
		"title": item.get("title"),
		"published_time": item.get("published_time"),
		"authors": item.get("authors"),
		"conference_metadata": item.get("conference_metadata"),
		"analysis": analysis,
	}

	if summary is not None:
		result_item["summary"] = summary
	else:
		result_item["summary"] = item.get("summary")

	for key in ("html_link", "link", "pdf_link"):
		if key in item:
			result_item[key] = item[key]

	return result_item


def run(
	input_json: Path,
	output_json: Path,
	download_dir: Path,
	limit: int | None,
	sleep_seconds: float,
	range_str: str | None,
) -> None:
	_ = download_dir
	items = load_input_items(input_json)

	if range_str:
		try:
			start, end = map(int, range_str.split("-"))
			start -= 1
			items = items[start:end]
		except ValueError:
			print(f"Invalid range format: {range_str}. Expected format: start-end")
			return

	results: list[dict[str, Any]] = []
	items_to_process = items[:limit] if limit is not None else items

	with ThreadPoolExecutor(max_workers=5) as executor:
		futures = [executor.submit(process_item, item) for item in items_to_process]
		for future in tqdm(futures, desc="Analyzing papers (input summary)", unit="paper"):
			result = future.result()
			if result:
				results.append(result)

	if range_str:
		range_info = {"range": range_str}
		results.insert(0, range_info)

	output_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
	_ = sleep_seconds


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Analyze world-model papers from input JSON summaries (no HTML fetch).",
	)
	parser.add_argument("--input", default="cs_cr_last_2_months.json", help="Input paper list JSON (must include summary)")
	parser.add_argument("--output", default="analysis_results.json", help="Output analysis JSON")
	parser.add_argument("--download-dir", default="downloads", help="Unused; kept for CLI compatibility")
	parser.add_argument("--limit", type=int, default=None, help="Analyze first N papers for quick runs")
	parser.add_argument("--sleep", type=float, default=0.2, help="Reserved; no per-item sleep in pool mode")
	parser.add_argument("--range", type=str, default=None, dest="range_str", help="Range of papers to analyze, e.g., 1-50")
	parser.add_argument("--model", help="Override AI_MODEL from .env")
	parser.add_argument("--base-url", help="Override AI_BASE_URL from .env")
	parser.add_argument("--api-key", help="Override AI_API_KEY from .env")
	args = parser.parse_args()

	if args.model:
		os.environ["AI_MODEL"] = args.model
	if args.base_url:
		os.environ["AI_BASE_URL"] = args.base_url
	if args.api_key:
		os.environ["AI_API_KEY"] = args.api_key

	model, base_url, api_key = get_ai_config()
	if not api_key:
		raise SystemExit("AI_API_KEY is missing. Run setup.ps1 to write .env.")
	print(f"Using model={model} base_url={base_url}")

	input_stem = Path(args.input).stem
	parts = input_stem.split("_")
	if len(parts) >= 3 and parts[1].isdigit() and len(parts[1]) == 4 and parts[2].isdigit():
		category = parts[0]
		year = parts[1]
		month = parts[2]
		base_name = f"analysis_{category}_{year}_{month}"
	else:
		base_name = f"analysis_{input_stem}"
	if args.range_str:
		new_output = f"{base_name}_{args.range_str}.json"
	else:
		new_output = f"{base_name}.json"
	args.output = new_output

	run(
		input_json=Path(args.input),
		output_json=Path(args.output),
		download_dir=Path(args.download_dir),
		limit=args.limit,
		sleep_seconds=args.sleep,
		range_str=args.range_str,
	)

	print(f"Saved analysis to {args.output}")


if __name__ == "__main__":
	main()
