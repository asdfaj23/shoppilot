"""
ShopPilot ① 配置层：pydantic-settings 读 .env（向下兼容 week1-4 的 config 用法）
================================================================================
设计原因：
  - 配置只有一个消费方原则：任何模块需要环境信息（key/模型/预算/路径）都从这里拿，
    禁止散落 os.getenv——换模型/换路径只改一处。
  - pydantic-settings 带类型与默认值校验；若环境里没装，退化成等价的轻量实现（可选依赖）。
  - 保留 API_KEY / BASE_URL / MODEL / get_client 模块级名字：archive/week1-4 老脚本不受影响。
.env 中配置（已被 .gitignore，不会提交到 Git）：
    DEEPSEEK_API_KEY   你的 API Key
    DEEPSEEK_BASE_URL  默认 https://api.deepseek.com/v1
    DEEPSEEK_MODEL     默认 deepseek-flash（即 DeepSeek-V4.1-Flash）
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)  # .env 优先于系统环境变量（防止机器上残留的旧 key 干扰）

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ECOM_DIR = DATA_DIR / "ecommerce"
SESSIONS_DIR = DATA_DIR / "sessions"
TRACES_DIR = ROOT / "traces"

try:  # pydantic-settings 是可选依赖：装了用它的类型校验，没装走下面的轻量兜底
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        deepseek_api_key: str = ""
        deepseek_base_url: str = "https://api.deepseek.com/v1"
        deepseek_model: str = "deepseek-flash"
        # Agent 预算（Harness 三重护栏）
        llm_temperature: float = 0.3
        max_steps: int = 8              # 单个子任务最多 ReAct 步数
        max_tokens_budget: int = 60000  # 单次任务全链路 token 预算
        max_cost_cny: float = 0.5       # 单次任务成本预算（按 flash 价格估算）
        price_per_m_tokens: float = 2.0  # 估算单价：元 / 百万 token
        subtask_timeout_s: int = 90     # 单工具/子任务超时
        max_retries: int = 2            # 失败重试次数（指数退避）

        model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    _settings = Settings(_env_file=str(ROOT / ".env"))  # type: ignore[call-arg]
except ImportError:  # 兜底实现：字段与 Settings 一致，仅无类型校验
    class _FallbackSettings:
        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.3"))
        max_steps = int(os.getenv("MAX_STEPS", "8"))
        max_tokens_budget = int(os.getenv("MAX_TOKENS_BUDGET", "60000"))
        max_cost_cny = float(os.getenv("MAX_COST_CNY", "0.5"))
        price_per_m_tokens = float(os.getenv("PRICE_PER_M_TOKENS", "2.0"))
        subtask_timeout_s = int(os.getenv("SUBTASK_TIMEOUT_S", "90"))
        max_retries = int(os.getenv("MAX_RETRIES", "2"))

    _settings = _FallbackSettings()  # type: ignore[assignment]

settings = _settings

# ---- week1-4 兼容出口（archive/ 老脚本 `from config import MODEL, get_client`） ----
API_KEY = settings.deepseek_api_key
BASE_URL = settings.deepseek_base_url
MODEL = settings.deepseek_model


def get_client():
    """所有脚本统一从这里拿 OpenAI 客户端（已指向 DeepSeek）。"""
    if not API_KEY:
        raise RuntimeError("未读到 DEEPSEEK_API_KEY：请在仓库根目录创建 .env（参考 .env.example）")
    from openai import OpenAI

    return OpenAI(api_key=API_KEY, base_url=BASE_URL)
