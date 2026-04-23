#!/usr/bin/env python3
"""Fetch recent arXiv cs.CR papers and save as a JSON list."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

try:
	from tqdm import tqdm
except ImportError:
	def tqdm(iterable, **kwargs):
		return iterable


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "SecPaper-DeepScan/1.0 (arXiv metadata fetcher)"


def build_query(months_back: int = 2) -> str:
	"""Build an arXiv API query for cs.CR papers submitted in the last N months."""
	end = dt.datetime.now(dt.UTC)
	# Approximate "two months" as 60 days for API date-range query.
	start = end - dt.timedelta(days=30 * months_back)

	start_str = start.strftime("%Y%m%d%H%M")
	end_str = end.strftime("%Y%m%d%H%M")
	return f"cat:cs.CR AND submittedDate:[{start_str} TO {end_str}]"


def _entry_text(entry: ET.Element, path: str) -> str | None:
	node = entry.find(path, ATOM_NS)
	if node is None or node.text is None:
		return None
	value = node.text.strip()
	return value or None


def parse_entry(entry: ET.Element) -> dict[str, Any]:
	"""Parse one arXiv Atom entry into the expected JSON object."""
	title = _entry_text(entry, "atom:title")
	published_time = _entry_text(entry, "atom:published")
	html_link = _entry_text(entry, "atom:id")
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

	if not pdf_link:
		abs_id = _entry_text(entry, "atom:id")
		if abs_id:
			pdf_link = abs_id.replace("/abs/", "/pdf/") + ".pdf"

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
	}


def _fetch_xml(url: str, max_retries: int = 5, base_backoff: float = 2.0) -> bytes:
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
	pbar = tqdm(desc="Fetching papers", unit="batch")

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


def write_json(data: list[dict[str, Any]], output_path: str) -> None:
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(data, f, ensure_ascii=False, indent=2)


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Fetch cs.CR papers from arXiv in the last two months and save as JSON list."
	)
	parser.add_argument(
		"--output",
		default="cs_cr_last_2_months.json",
		help="Output JSON file path (default: cs_cr_last_2_months.json)",
	)
	parser.add_argument(
		"--months",
		type=int,
		default=2,
		help="How many months back to query (default: 2)",
	)
	parser.add_argument(
		"--per-request",
		type=int,
		default=100,
		help="Results per arXiv request (default: 100)",
	)
	args = parser.parse_args()

	query = build_query(months_back=args.months)
	papers = fetch_papers(query=query, per_request=args.per_request)
	write_json(papers, args.output)

	print(f"Saved {len(papers)} papers to {args.output}")


if __name__ == "__main__":
	main()
