#!/usr/bin/env python3
"""Load AI settings from environment variables and an optional project .env file."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

DEFAULT_AI_MODEL = "qwen3.7-plus"
DEFAULT_AI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def load_dotenv(path: Path = ENV_PATH) -> None:
	if not path.is_file():
		return
	for raw in path.read_text(encoding="utf-8").splitlines():
		line = raw.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, value = line.split("=", 1)
		key = key.strip()
		value = value.strip().strip("'").strip('"')
		if key and key not in os.environ:
			os.environ[key] = value


def get_ai_config() -> tuple[str, str, str]:
	load_dotenv()
	model = (os.environ.get("AI_MODEL") or DEFAULT_AI_MODEL).strip()
	base_url = (os.environ.get("AI_BASE_URL") or DEFAULT_AI_BASE_URL).strip()
	api_key = (os.environ.get("AI_API_KEY") or "").strip()
	return model, base_url, api_key
