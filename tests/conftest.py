from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "data" / "samples"
LABELS = REPO_ROOT / "data" / "labels"


def load_label(stem: str) -> dict[str, Any]:
    label = json.loads((LABELS / f"{stem}.json").read_text(encoding="utf-8"))
    label.pop("file")
    return label


@pytest.fixture
def invoice_label() -> dict[str, Any]:
    return load_label("inv-001-classic")


@pytest.fixture
def po_label() -> dict[str, Any]:
    return load_label("po-011-classic")


@pytest.fixture
def broken_invoice(invoice_label: dict[str, Any]) -> dict[str, Any]:
    """The invoice with a wrong total, which the cross-field check must catch."""
    broken = copy.deepcopy(invoice_label)
    broken["total"] = round(broken["total"] + 10, 2)
    return broken


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "mock",
        "extraction_mode": "auto",
        "max_attempts": 3,
        "ollama_base_url": "http://127.0.0.1:9",
        "ollama_text_model": "llama3.2",
        "ollama_vision_model": "qwen2.5vl:3b",
        "llm_timeout_seconds": 5.0,
        "anthropic_api_key": "",
        "claude_model": "claude-haiku-5-5",
        "mock_responses_dir": tmp_path / "mock",
        "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "storage_dir": tmp_path / "pages",
        "max_pages": 4,
        "max_upload_mb": 15,
        "vision_max_side": 800,
        "log_level": "WARNING",
    }
    values.update(overrides)
    (tmp_path / "mock").mkdir(exist_ok=True)
    return Settings(**values)
