"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALID_PROVIDERS = ("ollama", "claude", "mock")
VALID_MODES = ("auto", "text", "vision")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader so the app needs no extra dependency.

    Real environment variables win over values in the file.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    extraction_mode: str
    max_attempts: int
    ollama_base_url: str
    ollama_text_model: str
    ollama_vision_model: str
    llm_timeout_seconds: float
    anthropic_api_key: str
    claude_model: str
    mock_responses_dir: Path
    database_url: str
    storage_dir: Path
    max_pages: int
    max_upload_mb: int
    vision_max_side: int
    log_level: str


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def get_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    env = os.environ.get

    provider = env("LLM_PROVIDER", "ollama").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {VALID_PROVIDERS}, got {provider!r}")
    mode = env("EXTRACTION_MODE", "auto").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"EXTRACTION_MODE must be one of {VALID_MODES}, got {mode!r}")

    database_url = env("DATABASE_URL", "sqlite:///var/extraction.db")
    # Make a relative SQLite path independent of the current working directory.
    prefix = "sqlite:///"
    if database_url.startswith(prefix) and not database_url.startswith("sqlite:////"):
        relative = database_url[len(prefix):]
        if relative != ":memory:" and not Path(relative).is_absolute():
            database_url = prefix + (REPO_ROOT / relative).as_posix()

    return Settings(
        llm_provider=provider,
        extraction_mode=mode,
        max_attempts=max(1, int(env("MAX_ATTEMPTS", "3"))),
        ollama_base_url=env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        ollama_text_model=env("OLLAMA_TEXT_MODEL", "llama3.2"),
        ollama_vision_model=env("OLLAMA_VISION_MODEL", "qwen2.5vl:3b"),
        llm_timeout_seconds=float(env("LLM_TIMEOUT_SECONDS", "900")),
        anthropic_api_key=env("ANTHROPIC_API_KEY", "").strip(),
        claude_model=env("CLAUDE_MODEL", "claude-haiku-5-5"),
        mock_responses_dir=_resolve(env("MOCK_RESPONSES_DIR", "data/mock_responses")),
        database_url=database_url,
        storage_dir=_resolve(env("STORAGE_DIR", "var/pages")),
        max_pages=max(1, int(env("MAX_PAGES", "4"))),
        max_upload_mb=max(1, int(env("MAX_UPLOAD_MB", "15"))),
        vision_max_side=max(256, int(env("VISION_MAX_SIDE", "1280"))),
        log_level=env("LOG_LEVEL", "INFO").upper(),
    )
