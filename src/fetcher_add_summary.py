#!/usr/bin/env python3
"""Fetch arXiv papers with API summaries, save as a JSON list."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
	from tqdm import tqdm
except ImportError:
	def tqdm(iterable, **kwargs):
		return iterable


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def build_query(category: str, year: int, month: int) -> str:
	"""Build an arXiv API query for papers submitted in a specific month and category."""
	if not (1 <= month <= 12):
		raise ValueError(f"Month must be between 1 and 12, got {month}")

	start_date = dt.datetime(year, month, 1, 0, 0, tzinfo=dt.UTC)
	if month == 12:
		end_date = dt.datetime(year + 1, 1, 1, 0, 0, tzinfo=dt.UTC)
	else:
		end_date = dt.datetime(year, month + 1, 1, 0, 0, tzinfo=dt.UTC)

	start_str = start_date.strftime("%Y%m%d%H%M")
	end_str = end_date.strftime("%Y%m%d%H%M")
	return f"cat:{category} AND submittedDate:[{start_str} TO {end_str}]"


def _entry_text(entry: ET.Element, path: str) -> str | None:
	node = entry.find(path, ATOM_NS)
	if node is None or node.text is None:
		return None
	value = re.sub(r"\s+", " ", node.text).strip()
	return value or None


def parse_entry(entry: ET.Element) -> dict[str, Any]:
	"""Parse one arXiv Atom entry into the expected JSON object, including summary."""
	title = _entry_text(entry, "atom:title")
	published_time = _entry_text(entry, "atom:published")
	html_link = _entry_text(entry, "atom:id")
	summary = _entry_text(entry, "atom:summary")
	authors = [
		author_name.text.strip()
		for author in entry.findall("atom:author", ATOM_NS)
		if (author_name := author.find("atom:name", ATOM_NS)) is not None and author_name.text
	]

	pdf_link = None
	for link in entry.findall("atom:link", ATOM_NS):
		if link.attrib.get("type") == "application/pdf":
			pdf_link = link.attrib.get("href")
			break

	if not pdf_link and html_link:
		pdf_link = html_link.replace("/abs/", "/pdf/") + ".pdf"

	journal_ref = _entry_text(entry, "arxiv:journal_ref")
	comment = _entry_text(entry, "arxiv:comment")
	doi = _entry_text(entry, "arxiv:doi")

	conference_metadata = None
	if any([journal_ref, comment, doi]):
		conference_metadata = {
			"journal_ref": journal_ref,
			"comment": comment,
			"doi": doi,
		}

	return {
		"title": title,
		"published_time": published_time,
		"authors": authors,
		"pdf_link": pdf_link,
		"html_link": html_link,
		"conference_metadata": conference_metadata,
		"summary": summary,
	}


def _fetch_xml(url: str, max_retries: int = 2, base_backoff: float = 10.0) -> bytes:
	"""Fetch XML with retries for transient network/rate-limit failures."""
	request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

	for attempt in range(max_retries):
		try:
			with urllib.request.urlopen(request) as response:
				return response.read()
		except urllib.error.HTTPError as exc:
			if exc.code == 429 and attempt < max_retries - 1:
				wait_seconds = base_backoff * (2**attempt)
				time.sleep(wait_seconds)
				continue
			raise

	raise RuntimeError("Failed to fetch arXiv XML after retries")


def fetch_papers(query: str, per_request: int = 100, pause_seconds: float = 3.0) -> list[dict[str, Any]]:
	"""Fetch all papers matching an arXiv query with pagination."""
	papers: list[dict[str, Any]] = []
	start = 0
	pbar = tqdm(desc="Fetching papers with summaries", unit="batch")

	while True:
		params = {
			"search_query": query,
			"start": start,
			"max_results": per_request,
			"sortBy": "submittedDate",
			"sortOrder": "descending",
		}
		url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
		raw_xml = _fetch_xml(url)

		root = ET.fromstring(raw_xml)
		entries = root.findall("atom:entry", ATOM_NS)
		if not entries:
			pbar.close()
			break

		papers.extend(parse_entry(entry) for entry in entries)
		start += len(entries)
		pbar.update(1)

		if len(entries) < per_request:
			pbar.close()
			break

		time.sleep(pause_seconds)

	return papers


def write_json(papers: list[dict[str, Any]], category: str, year: int, month: int, results_dir: Path) -> Path:
	"""Write papers to JSON file with metadata in the results directory."""
	results_dir.mkdir(parents=True, exist_ok=True)

	fetch_time = dt.datetime.now(dt.UTC).isoformat()
	total_count = len(papers)
	output_data = {
		"metadata": {
			"fetch_time": fetch_time,
			"total_count": total_count,
			"category": category,
			"year": year,
			"month": month,
			"includes_summary": True,
			"summary_source": "arxiv_api_atom_summary",
		},
		"papers": papers,
	}

	filename = f"{category}_{year}_{month:02d}_{total_count}_summary.json"
	output_path = results_dir / filename
	output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
	return output_path


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Fetch arXiv papers from a specified category and period, including API summaries."
	)
	parser.add_argument(
		"--category",
		default="cs.CR",
		help="arXiv category to fetch from (default: cs.CR)",
	)
	parser.add_argument(
		"--year",
		type=int,
		required=True,
		help="Year to fetch papers from (required)",
	)
	parser.add_argument(
		"--month",
		type=int,
		required=True,
		help="Month to fetch papers from (1-12, required)",
	)
	parser.add_argument(
		"--per-request",
		type=int,
		default=100,
		help="Results per arXiv request (default: 100)",
	)
	parser.add_argument(
		"--pause",
		type=float,
		default=3.0,
		help="Seconds to pause between each arXiv request (default: 3.0)",
	)
	args = parser.parse_args()

	src_dir = Path(__file__).parent
	results_dir = src_dir.parent / "results"

	query = build_query(category=args.category, year=args.year, month=args.month)
	papers = fetch_papers(query=query, per_request=args.per_request, pause_seconds=args.pause)
	output_path = write_json(papers, args.category, args.year, args.month, results_dir)

	print(f"Saved {len(papers)} papers with summaries to {output_path}")


if __name__ == "__main__":
	main()
