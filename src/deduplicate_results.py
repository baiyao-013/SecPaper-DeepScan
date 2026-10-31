#!/usr/bin/env python3
"""Deduplicate and keyword-filter paper JSON files from the results directory."""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_module
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
	from tqdm import tqdm
except ImportError:
	def tqdm(iterable, **kwargs):
		return iterable


USER_AGENT = "SecPaper-DeepScan/1.0 (deduplicate-results)"
TEMP_DIRS = ("results_temp01", "results_temp02", "results_temp03")


def load_json(path: Path) -> dict[str, Any]:
	data = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(data, dict) or not isinstance(data.get("papers"), list):
		raise ValueError(f"{path} must be a JSON object with a 'papers' list")
	return data


def ensure_temp_dirs(root_dir: Path) -> dict[str, Path]:
	paths: dict[str, Path] = {}
	for name in TEMP_DIRS:
		path = root_dir / name
		path.mkdir(parents=True, exist_ok=True)
		paths[name] = path
	return paths


def resolve_results_file(value: str, results_dir: Path, root_dir: Path) -> Path:
	path = Path(value)
	if not path.is_absolute():
		path = results_dir / path if path.parent == Path(".") else root_dir / path
	path = path.resolve()
	results_dir = results_dir.resolve()

	if path.parent != results_dir:
		raise ValueError(f"Input file must be directly under results directory: {value}")
	if path.suffix.lower() != ".json":
		raise ValueError(f"Input file must be a JSON file: {value}")
	if not path.exists():
		raise FileNotFoundError(path)
	return path


def parse_arxiv_core_id(url: str | None) -> str | None:
	if not url:
		return None
	m = re.search(r"/(?:abs|pdf)/([^/?#]+)", url)
	if not m:
		return None
	paper_id = m.group(1)
	if paper_id.endswith(".pdf"):
		paper_id = paper_id[:-4]
	paper_id = re.sub(r"v\d+$", "", paper_id)
	return paper_id or None


def paper_core_id(paper: dict[str, Any]) -> str | None:
	for key in ("html_link", "pdf_link", "link"):
		value = paper.get(key)
		if isinstance(value, str):
			paper_id = parse_arxiv_core_id(value)
			if paper_id:
				return paper_id
	return None


def metadata_time(data: dict[str, Any], path: Path) -> tuple[str, str]:
	metadata = data.get("metadata")
	if isinstance(metadata, dict):
		year = metadata.get("year")
		month = metadata.get("month")
		if year is not None and month is not None:
			return str(year), f"{int(month):02d}"

	parts = path.stem.split("_")
	if len(parts) >= 3:
		return parts[1], f"{int(parts[2]):02d}"
	raise ValueError(f"Cannot determine year/month from {path}")


def metadata_category(data: dict[str, Any], path: Path) -> str:
	metadata = data.get("metadata")
	if isinstance(metadata, dict) and metadata.get("category"):
		return str(metadata["category"])
	parts = path.stem.split("_")
	if parts:
		return parts[0]
	raise ValueError(f"Cannot determine category from {path}")


def combined_category(categories: list[str]) -> str:
	if not categories:
		raise ValueError("At least one category is required to build a combined category name")
	if len(categories) == 1:
		return categories[0]

	first_prefix, _, first_suffix = categories[0].partition(".")
	suffixes: list[str] = [first_suffix]
	share_prefix = bool(first_prefix and first_suffix)
	for category in categories[1:]:
		prefix, _, suffix = category.partition(".")
		if not (share_prefix and prefix == first_prefix and suffix):
			share_prefix = False
			break
		suffixes.append(suffix)

	if share_prefix:
		return f"{first_prefix}.{'-'.join(suffixes)}"
	return "-".join(categories)


def output_filename(category: str, year: str, month: str, count: int) -> str:
	return f"{category}_{year}_{month}_{count}.json"


def build_metadata(
	source_paths: list[Path],
	category: str,
	year: str,
	month: str,
	papers: list[dict[str, Any]],
	operation: str,
	keywords: list[str] | None,
) -> dict[str, Any]:
	metadata: dict[str, Any] = {
		"generated_time": dt.datetime.now(dt.UTC).isoformat(),
		"total_count": len(papers),
		"category": category,
		"year": int(year),
		"month": int(month),
		"operation": operation,
		"source_files": [str(path) for path in source_paths],
	}
	if keywords is not None:
		metadata["keywords"] = keywords
	return metadata


def write_result(
	output_dir: Path,
	filename: str,
	metadata: dict[str, Any],
	papers: list[dict[str, Any]],
) -> Path:
	output_path = output_dir / filename
	output = {
		"metadata": metadata,
		"papers": papers,
	}
	output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
	return output_path


def deduplicate_first_against_others(
	first_papers: list[dict[str, Any]],
	other_papers_lists: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
	excluded_ids: set[str] = set()
	for papers in other_papers_lists:
		for paper in papers:
			paper_id = paper_core_id(paper)
			if paper_id:
				excluded_ids.add(paper_id)
	return [paper for paper in first_papers if paper_core_id(paper) not in excluded_ids]


def load_keywords(args: argparse.Namespace) -> list[str] | None:
	raw: str | None = args.keyword_groups
	if args.keyword_groups_file:
		raw = Path(args.keyword_groups_file).read_text(encoding="utf-8")
	if raw is None:
		return None

	parsed = json.loads(raw)
	if not isinstance(parsed, list):
		raise ValueError('Keywords must be a JSON list, for example: ["attack", "jailbreak"]')

	if not all(isinstance(item, str) for item in parsed):
		raise ValueError("Every keyword must be a string")

	keywords = [item.strip() for item in parsed if item.strip()]
	if not keywords:
		raise ValueError("At least one keyword is required when keywords are specified")
	return keywords


def normalize_keywords(keywords: list[str]) -> list[str]:
	return [keyword.casefold() for keyword in keywords if keyword.strip()]


def derive_html_link(paper: dict[str, Any]) -> str | None:
	html_link = paper.get("html_link")
	if isinstance(html_link, str) and html_link:
		return html_link
	for key in ("pdf_link", "link"):
		value = paper.get(key)
		if not isinstance(value, str):
			continue
		paper_id = parse_arxiv_core_id(value)
		if paper_id:
			return f"https://arxiv.org/abs/{paper_id}"
	return None


def request_text(url: str, timeout: int) -> str:
	request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
	with urllib.request.urlopen(request, timeout=timeout) as response:
		return response.read().decode("utf-8", errors="replace")


def extract_summary(abs_html: str) -> str | None:
	patterns = [
		r'<blockquote class="abstract[^\"]*">(.*?)</blockquote>',
		r'<div class="abstract[^\"]*">(.*?)</div>',
	]
	for pattern in patterns:
		match = re.search(pattern, abs_html, flags=re.IGNORECASE | re.DOTALL)
		if not match:
			continue
		chunk = re.sub(r"<[^>]+>", " ", match.group(1))
		chunk = html_module.unescape(chunk)
		chunk = re.sub(r"\s+", " ", chunk).strip()
		chunk = re.sub(r"^Abstract:\s*", "", chunk, flags=re.IGNORECASE)
		if chunk:
			return chunk
	return None


def fetch_summary(paper: dict[str, Any], timeout: int, retries: int, retry_backoff: float) -> tuple[str | None, str | None]:
	html_link = derive_html_link(paper)
	if not html_link:
		return None, "no_html_link"

	for attempt in range(retries + 1):
		try:
			summary = extract_summary(request_text(html_link, timeout=timeout))
			if summary:
				return summary, None
			return None, "abstract_not_found_in_html"
		except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
			if attempt < retries:
				time.sleep(retry_backoff * (2**attempt))
				continue
			return None, f"fetch_failed:{type(exc).__name__}"
	return None, "fetch_failed"


def existing_summary(paper: dict[str, Any]) -> str | None:
	for key in ("summary", "abstract", "html_abstract"):
		value = paper.get(key)
		if isinstance(value, str) and value.strip():
			return re.sub(r"\s+", " ", value).strip()
	return None


def keyword_filter_papers(
	papers: list[dict[str, Any]],
	keywords: list[str],
	pause_seconds: float,
	timeout: int,
	retries: int,
	retry_backoff: float,
) -> list[dict[str, Any]]:
	normalized_keywords = normalize_keywords(keywords)
	filtered: list[dict[str, Any]] = []

	for index, paper in enumerate(tqdm(papers, desc="Fetching summaries and filtering", unit="paper")):
		output_paper = dict(paper)
		summary = existing_summary(output_paper)
		fetched_summary = False
		if summary is None:
			summary, _ = fetch_summary(output_paper, timeout=timeout, retries=retries, retry_backoff=retry_backoff)
			fetched_summary = True
		output_paper["summary"] = summary

		text = f"{output_paper.get('title') or ''}\n{summary or ''}".casefold()
		if any(keyword in text for keyword in normalized_keywords):
			filtered.append(output_paper)

		if fetched_summary and pause_seconds > 0 and index < len(papers) - 1:
			time.sleep(pause_seconds)

	return filtered


def validate_same_year_month(
	all_data: list[dict[str, Any]],
	all_paths: list[Path],
) -> tuple[str, str]:
	if not all_data:
		raise ValueError("At least one input file is required for time validation")
	first_time = metadata_time(all_data[0], all_paths[0])
	for data, path in zip(all_data[1:], all_paths[1:]):
		current_time = metadata_time(data, path)
		if current_time != first_time:
			raise ValueError(
				f"Refuse to compare files with different time ranges: "
				f"{all_paths[0].name} is {first_time[0]}-{first_time[1]}, "
				f"{path.name} is {current_time[0]}-{current_time[1]}"
			)
	return first_time


def run(args: argparse.Namespace) -> list[Path]:
	root_dir = Path(__file__).resolve().parent.parent
	results_dir = root_dir / "results"
	temp_dirs = ensure_temp_dirs(root_dir)
	input_paths = [resolve_results_file(value, results_dir, root_dir) for value in args.inputs]
	keywords = load_keywords(args)

	if not input_paths:
		raise ValueError("Provide at least one JSON file from the results directory")
	if len(input_paths) == 1 and keywords is None:
		raise ValueError("A keyword list is required when only one input file is provided")

	written_paths: list[Path] = []
	first_data = load_json(input_paths[0])
	first_papers = first_data["papers"]
	first_category = metadata_category(first_data, input_paths[0])
	year, month = metadata_time(first_data, input_paths[0])

	if len(input_paths) == 1:
		assert keywords is not None
		filtered = keyword_filter_papers(
			first_papers,
			keywords,
			pause_seconds=args.pause,
			timeout=args.timeout,
			retries=args.retries,
			retry_backoff=args.retry_backoff,
		)
		filename = output_filename(first_category, year, month, len(filtered))
		metadata = build_metadata(input_paths, first_category, year, month, filtered, "keyword_filter", keywords)
		written_paths.append(write_result(temp_dirs["results_temp01"], filename, metadata, filtered))
		return written_paths

	other_data_list = [load_json(path) for path in input_paths[1:]]
	all_data_list = [first_data, *other_data_list]
	year, month = validate_same_year_month(all_data_list, input_paths)
	other_categories = [
		metadata_category(data, path) for data, path in zip(other_data_list, input_paths[1:])
	]
	output_category = combined_category([first_category, *other_categories])

	other_papers_lists = [data["papers"] for data in other_data_list]
	deduplicated = deduplicate_first_against_others(first_papers, other_papers_lists)
	temp02_filename = output_filename(output_category, year, month, len(deduplicated))
	temp02_metadata = build_metadata(input_paths, output_category, year, month, deduplicated, "deduplicate", None)
	written_paths.append(write_result(temp_dirs["results_temp02"], temp02_filename, temp02_metadata, deduplicated))

	if keywords is None:
		return written_paths

	filtered = keyword_filter_papers(
		deduplicated,
		keywords,
		pause_seconds=args.pause,
		timeout=args.timeout,
		retries=args.retries,
		retry_backoff=args.retry_backoff,
	)
	temp03_filename = output_filename(output_category, year, month, len(filtered))
	temp03_metadata = build_metadata(input_paths, output_category, year, month, filtered, "deduplicate_then_keyword_filter", keywords)
	written_paths.append(write_result(temp_dirs["results_temp03"], temp03_filename, temp03_metadata, filtered))
	return written_paths


def main() -> None:
	parser = argparse.ArgumentParser(
		description=(
			"Deduplicate results/*.json by arXiv core ID and optionally filter by title/summary keywords. "
			"With two or more inputs, the first file is subtracted by the union of all subsequent files."
		),
	)
	parser.add_argument(
		"inputs",
		nargs="+",
		help=(
			"One or more JSON filenames/paths directly under results/. "
			"With 2+ inputs, the first is kept and any paper whose arXiv core ID also appears in any of the "
			"remaining files is removed. All inputs must share the same year/month."
		),
	)
	parser.add_argument(
		"--keyword-groups",
		help='Keywords as a JSON list, for example: ["attack","jailbreak","LLM"]',
	)
	parser.add_argument("--keyword-groups-file", help="Path to a UTF-8 JSON file containing a keyword list")
	parser.add_argument("--pause", type=float, default=3.0, help="Seconds to wait between summary requests (default: 3.0)")
	parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds for summary requests (default: 30)")
	parser.add_argument("--retries", type=int, default=2, help="Retry count for summary requests (default: 2)")
	parser.add_argument(
		"--retry-backoff",
		type=float,
		default=5.0,
		help="Base seconds for exponential retry backoff (default: 5.0)",
	)
	args = parser.parse_args()

	try:
		output_paths = run(args)
	except Exception as exc:  # noqa: BLE001
		raise SystemExit(f"Error: {exc}") from exc

	for path in output_paths:
		print(f"Saved {path}")


if __name__ == "__main__":
	main()
