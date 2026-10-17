"""
统一配置：DeepSeek API（完全兼容 OpenAI SDK，只需把 base_url 指过去）
====================================================================
.env 中配置（已被 .gitignore，不会提交到 Git）：
    DEEPSEEK_API_KEY   你的 API Key
    DEEPSEEK_BASE_URL  默认 https://api.deepseek.com/v1
    DEEPSEEK_MODEL     默认 deepseek-flash（即 DeepSeek-V4.1-Flash）
"""
import os

from dotenv import load_dotenv

load_dotenv(override=True)  # .env 优先于系统环境变量（防止机器上残留的旧 key 干扰）

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

if not API_KEY:
    raise RuntimeError("未读到 DEEPSEEK_API_KEY：请在仓库根目录创建 .env（参考 .env.example）")


def get_client():
    """所有脚本统一从这里拿 OpenAI 客户端（已指向 DeepSeek）。"""
    from openai import OpenAI

    return OpenAI(api_key=API_KEY, base_url=BASE_URL)
