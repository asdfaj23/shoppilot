"""Central configuration.

Single-consumer principle: every module that needs an environment value (API key,
model, budget, filesystem path) gets it from :class:`Settings` instead of scattering
``os.getenv`` calls. Settings can be overridden by environment variables (``.env``)
and, for tests / containers, by constructing and injecting a ``Settings`` explicitly.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# src/shoppilot/core/settings.py -> repo root (core -> shoppilot -> src -> root)
REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env", override=True)

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        # --- LLM provider (OpenAI-compatible; defaults point at DeepSeek) ---
        deepseek_api_key: str = ""
        deepseek_base_url: str = "https://api.deepseek.com/v1"
        deepseek_model: str = "deepseek-chat"
        llm_temperature: float = 0.3

        # --- Filesystem layout (overridable via env for containers / tests) ---
        data_dir: Path = REPO_ROOT / "data"
        traces_dir: Path = REPO_ROOT / "traces"

        # --- Bounded ReAct budgets ---
        max_steps: int = 8
        max_tokens_budget: int = 60_000
        max_cost_cny: float = 0.5
        price_per_m_tokens: float = 2.0
        subtask_timeout_s: int = 90
        max_retries: int = 2

        model_config = SettingsConfigDict(
            env_file=str(REPO_ROOT / ".env"),
            extra="ignore",
            case_sensitive=False,
        )

except ImportError:  # pragma: no cover - graceful fallback when pydantic-settings absent

    class Settings:  # type: ignore[no-redef]
        import os

        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.3"))
        data_dir = Path(os.getenv("SHOPPILOT_DATA_DIR", str(REPO_ROOT / "data")))
        traces_dir = Path(os.getenv("SHOPPILOT_TRACES_DIR", str(REPO_ROOT / "traces")))
        max_steps = int(os.getenv("MAX_STEPS", "8"))
        max_tokens_budget = int(os.getenv("MAX_TOKENS_BUDGET", "60000"))
        max_cost_cny = float(os.getenv("MAX_COST_CNY", "0.5"))
        price_per_m_tokens = float(os.getenv("PRICE_PER_M_TOKENS", "2.0"))
        subtask_timeout_s = int(os.getenv("SUBTASK_TIMEOUT_S", "90"))
        max_retries = int(os.getenv("MAX_RETRIES", "2"))


def get_settings() -> Settings:
    """Build a fresh settings object (re-reads env). Useful in tests / containers."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()

# ---------------------------------------------------------------------------
# Derived paths / convenience constants (process-wide defaults).
# Prefer injecting ``Settings`` into adapters; these are kept stable for internal
# modules that only need the default data / traces locations.
# ---------------------------------------------------------------------------
DATA_DIR = Path(settings.data_dir)
ECOM_DIR = DATA_DIR / "ecommerce"
SESSIONS_DIR = DATA_DIR / "sessions"
TRACES_DIR = Path(settings.traces_dir)

API_KEY = settings.deepseek_api_key
BASE_URL = settings.deepseek_base_url
MODEL = settings.deepseek_model


def get_client():
    """Return a raw OpenAI-compatible client (used by standalone scripts / MCP)."""
    if not API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not found: create a .env at the repo root (see .env.example)."
        )
    from openai import OpenAI

    return OpenAI(api_key=API_KEY, base_url=BASE_URL)
