#!/usr/bin/env python3
"""Download arXiv papers from JSON and run AI analysis for vulnerability research signals."""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
	from tqdm import tqdm
except ImportError:
	def tqdm(iterable, **kwargs):
		return iterable


USER_AGENT = "SecPaper-DeepScan/1.0 (paper analyzer)"
AI_MODEL = "deepseek-reasoner"
AI_BASE_URL = "https://api.deepseek.com"
AI_API_KEY = "sk-ca6cbdfc375243aeb60203a006d5f15c"


@dataclass
class DownloadResult:
	doc_type: str
	doc_path: str | None
	doc_url: str | None
	abstract_text: str | None


def safe_filename(value: str, max_len: int = 120) -> str:
	cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_.")
	if not cleaned:
		cleaned = "paper"
	return cleaned[:max_len]


def request_bytes(url: str, timeout: int = 30) -> bytes:
	req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
	with urllib.request.urlopen(req, timeout=timeout) as resp:
		return resp.read()


def parse_arxiv_id_from_url(url: str | None) -> str | None:
	if not url:
		return None
	# Supports /abs/2501.12345v2 and /pdf/2501.12345v2.pdf
	m = re.search(r"/(?:abs|pdf)/([^/?#]+)", url)
	if not m:
		return None
	paper_id = m.group(1)
	if paper_id.endswith(".pdf"):
		paper_id = paper_id[:-4]
	return paper_id


def derive_links(item: dict[str, Any]) -> tuple[str | None, str | None]:
	pdf_link = item.get("pdf_link")
	html_link = item.get("html_link")
	if html_link and isinstance(html_link, str):
		return pdf_link, html_link

	paper_id = parse_arxiv_id_from_url(pdf_link)
	if paper_id:
		return pdf_link, f"https://arxiv.org/abs/{paper_id}"

	return pdf_link, html_link


def extract_abstract_from_abs_html(html_text: str) -> str | None:
	# Try modern arXiv class and legacy blockquote pattern.
	patterns = [
		r'<blockquote class="abstract[^\"]*">(.*?)</blockquote>',
		r'<div class="abstract[^\"]*">(.*?)</div>',
	]
	for pattern in patterns:
		m = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
		if not m:
			continue
		chunk = m.group(1)
		chunk = re.sub(r"<[^>]+>", " ", chunk)
		chunk = html.unescape(chunk)
		chunk = re.sub(r"\s+", " ", chunk).strip()
		chunk = re.sub(r"^Abstract:\s*", "", chunk, flags=re.IGNORECASE)
		if chunk:
			return chunk
	return None


def download_paper_content(item: dict[str, Any], download_dir: Path) -> DownloadResult:
	title = str(item.get("title") or "paper")
	pdf_link, html_link = derive_links(item)

	paper_id = parse_arxiv_id_from_url(pdf_link) or parse_arxiv_id_from_url(html_link)
	base_name = safe_filename(paper_id or title)

	if pdf_link:
		pdf_path = download_dir / f"{base_name}.pdf"
		try:
			pdf_bytes = request_bytes(pdf_link)
			pdf_path.write_bytes(pdf_bytes)
			return DownloadResult(
				doc_type="pdf",
				doc_path=str(pdf_path),
				doc_url=pdf_link,
				abstract_text=None,
			)
		except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
			pass

	if html_link:
		html_path = download_dir / f"{base_name}.html"
		html_bytes = request_bytes(html_link)
		html_path.write_bytes(html_bytes)
		html_text = html_bytes.decode("utf-8", errors="replace")
		abstract = extract_abstract_from_abs_html(html_text)
		return DownloadResult(
			doc_type="html",
			doc_path=str(html_path),
			doc_url=html_link,
			abstract_text=abstract,
		)

	return DownloadResult(doc_type="none", doc_path=None, doc_url=None, abstract_text=None)


def build_analysis_prompt(item: dict[str, Any], abstract_text: str | None) -> str:
	context = {
		"title": item.get("title"),
		"published_time": item.get("published_time"),
		"authors": item.get("authors"),
		"conference_metadata": item.get("conference_metadata"),
		"abstract": abstract_text,
	}

	return (
		"你是网络安全论文分析助手。请根据输入论文信息输出严格 JSON，对以下问题进行判断："
		"1) 是否涉及漏洞发现/漏洞利用/漏洞防御，若涉及给出一个或多个类型；"
		"2) 具体使用的方法，是否创新；若创新，说明相对传统方法(如静态分析、动态分析、fuzzing、符号执行、人工规则检测等)的创新点；"
		"3) 该方法针对的漏洞类型。"
		"\n\n"
		"只允许输出 JSON，不要输出解释文字。"
		"JSON schema: "
		"{"
		'"in_scope": true/false, '
		'"categories": ["vuln_discovery"|"vuln_exploitation"|"vuln_defense"], '
		'"methods": [string], '
		'"is_innovative": true/false/null, '
		'"innovation_summary": string|null, '
		'"target_vulnerability_types": [string], '
		'"confidence": "high"|"medium"|"low", '
		'"evidence": [string]'
		"}"
		"\n\n"
		f"输入数据: {json.dumps(context, ensure_ascii=False)}"
	)


def call_openai_compatible(prompt: str, model: str, api_key: str, base_url: str) -> dict[str, Any]:
	url = base_url.rstrip("/") + "/chat/completions"
	payload = {
		"model": model,
		"temperature": 0.1,
		"response_format": {"type": "json_object"},
		"messages": [
			{
				"role": "system",
				"content": "你是一个严谨的网络安全研究分析器。",
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
		# Some providers return content blocks.
		content = "".join(block.get("text", "") for block in content if isinstance(block, dict))

	return json.loads(content)


def heuristic_analysis(item: dict[str, Any], abstract_text: str | None) -> dict[str, Any]:
	text = " ".join(
		[
			str(item.get("title") or ""),
			json.dumps(item.get("conference_metadata") or {}, ensure_ascii=False),
			abstract_text or "",
		]
	).lower()

	cat = []
	if any(k in text for k in ["exploit", "attack vector", "rce", "privilege escalation"]):
		cat.append("vuln_exploitation")
	if any(k in text for k in ["detect", "defense", "mitigation", "hardening", "protection"]):
		cat.append("vuln_defense")
	if any(k in text for k in ["find", "discovery", "fuzz", "static analysis", "dynamic analysis"]):
		cat.append("vuln_discovery")

	in_scope = len(cat) > 0
	methods = []
	for kw in ["fuzz", "symbolic", "static analysis", "dynamic analysis", "llm", "graph neural", "taint"]:
		if kw in text:
			methods.append(kw)

	vuln_types = []
	for kw in ["xss", "sql injection", "buffer overflow", "race condition", "can bus", "side-channel", "authentication"]:
		if kw in text:
			vuln_types.append(kw)

	return {
		"in_scope": in_scope,
		"categories": sorted(set(cat)),
		"methods": sorted(set(methods)),
		"is_innovative": None,
		"innovation_summary": "LLM不可用时的启发式结果，创新性判断不可靠。",
		"target_vulnerability_types": sorted(set(vuln_types)),
		"confidence": "low",
		"evidence": ["Heuristic keyword matching on title/metadata/abstract"],
	}


def analyze_item(item: dict[str, Any], download_result: DownloadResult) -> dict[str, Any]:
	model = AI_MODEL
	api_key = AI_API_KEY
	base_url = AI_BASE_URL

	if api_key and api_key != "REPLACE_WITH_YOUR_API_KEY":
		prompt = build_analysis_prompt(item, download_result.abstract_text)
		try:
			return call_openai_compatible(prompt, model=model, api_key=api_key, base_url=base_url)
		except Exception as exc:  # noqa: BLE001
			fallback = heuristic_analysis(item, download_result.abstract_text)
			fallback["evidence"].append(f"LLM call failed: {type(exc).__name__}")
			return fallback

	return heuristic_analysis(item, download_result.abstract_text)


def run(input_json: Path, output_json: Path, download_dir: Path, limit: int | None, sleep_seconds: float) -> None:
	items = json.loads(input_json.read_text(encoding="utf-8"))
	if not isinstance(items, list):
		raise ValueError("Input JSON must be a list")

	download_dir.mkdir(parents=True, exist_ok=True)
	results: list[dict[str, Any]] = []

	items_to_process = items[:limit] if limit is not None else items
	for item in tqdm(items_to_process, desc="Analyzing papers", unit="paper"):
		if not isinstance(item, dict):
			continue

		download = download_paper_content(item, download_dir)
		analysis = analyze_item(item, download)

		results.append(
			{
				"title": item.get("title"),
				"published_time": item.get("published_time"),
				"authors": item.get("authors"),
				"pdf_link": item.get("pdf_link"),
				"html_link": item.get("html_link"),
				"conference_metadata": item.get("conference_metadata"),
				"download": {
					"doc_type": download.doc_type,
					"doc_path": download.doc_path,
					"doc_url": download.doc_url,
				},
				"analysis": analysis,
			}
		)

		if sleep_seconds > 0:
			time.sleep(sleep_seconds)

	output_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
	parser = argparse.ArgumentParser(description="Download papers and analyze vulnerability research relevance.")
	parser.add_argument("--input", default="cs_cr_last_2_months.json", help="Input paper list JSON")
	parser.add_argument("--output", default="analysis_results.json", help="Output analysis JSON")
	parser.add_argument("--download-dir", default="downloads", help="Directory for downloaded files")
	parser.add_argument("--limit", type=int, default=None, help="Analyze first N papers for quick runs")
	parser.add_argument("--sleep", type=float, default=0.2, help="Sleep seconds between items")
	args = parser.parse_args()

	run(
		input_json=Path(args.input),
		output_json=Path(args.output),
		download_dir=Path(args.download_dir),
		limit=args.limit,
		sleep_seconds=args.sleep,
	)

	print(f"Saved analysis to {args.output}")


if __name__ == "__main__":
	main()
