"""读取环境变量配置。LLM 走 OpenAI 兼容接口，默认指向 DeepSeek。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

# 尝试加载项目根目录的 .env（没装 python-dotenv 时静默跳过，仍可用系统环境变量）
try:  # pragma: no cover
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:  # 纯标准库 stub 模式不需要 dotenv
    pass


def get_settings() -> Dict[str, str]:
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        # 留空 = 不传，用服务端默认；评测与打分建议设 0 以降低波动
        "temperature": os.getenv("LLM_TEMPERATURE", ""),
    }
