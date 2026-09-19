"""Configuration for the book generation system"""

import os
import re
from typing import Dict

from dotenv import load_dotenv

load_dotenv()


def _get_env(names, default=None):
    """Read the first environment variable found from a list of candidates."""
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


def _is_reasoning_model(model: str) -> bool:
    """Detect OpenAI-style reasoning models that require max_completion_tokens.

    These models also reject most sampling overrides (temperature must be 1).
    """
    model = model.lower()
    if any(token in model for token in ("gpt-5", "gpt-6", "reasoning")):
        return True
    # Matches slug tokens such as o1, o1-mini, o3, o4-mini (and o2/o5/o6).
    return re.search(r"(^|[^a-z0-9])o[1-6](\b|-)", model) is not None


def get_config() -> Dict:
    """Get the configuration for the agents"""

    # Basic config for local LLM
    config_list = [
        {
            "model": os.getenv("MODEL", "google/gemini-2.5-flash"),
            "base_url": os.getenv("BASE_URL", "https://openrouter.ai/api/v1"),
            "api_key": os.getenv("API_KEY"),
        }
    ]

    model = config_list[0]["model"]

    # Choose which token limit parameter the API call should use.
    # max_completion_tokens is required by OpenAI reasoning models (o1/o3/gpt-5+),
    # while most other models and OpenAI-compatible servers expect max_tokens.
    token_param = (_get_env(["TOKEN_PARAM"], "auto") or "auto").strip().lower()
    if token_param not in ("max_tokens", "max_completion_tokens"):
        token_param = "max_completion_tokens" if _is_reasoning_model(model) else "max_tokens"

    # Temperature default is 1, which is the only value OpenAI reasoning models accept.
    temperature = float(_get_env(["TEMPERATURE", "temperature"], 1))
    if token_param == "max_completion_tokens":
        temperature = 1.0

    # Common configuration for all agents
    agent_config = {
        "seed": int(_get_env(["SEED"], 42)),
        "temperature": temperature,
        "top_p": float(_get_env(["TOP_P"], 1.0)),
        "config_list": config_list,
        "timeout": int(_get_env(["TIMEOUT"], 1000)),
        "cache_seed": None,
        "token_param": token_param,
        "max_tokens": int(
            _get_env(
                ["MAX_TOKENS", "max_tokens", "MAX_COMPLETION_TOKENS", "max_completion_tokens"],
                10000,
            )
        ),
    }

    return agent_config