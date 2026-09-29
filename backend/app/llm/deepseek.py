"""DeepSeek 客户端：主力对话模型。

默认 base_url=https://api.deepseek.com，模型 deepseek-v4-flash。
"""
from __future__ import annotations
from typing import Literal

from app.config import Settings
from app.llm.client import LLMClient


class DeepSeekClient(LLMClient):
    def __init__(self, settings: Settings, *, thinking: Literal["enabled", "disabled"] | None = None) -> None:
        self.thinking = thinking
        super().__init__(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            timeout=settings.deepseek_timeout,
            temperature=settings.deepseek_temperature,
            max_tokens=settings.deepseek_max_tokens,
        )

    def name(self) -> str:
        return "deepseek"

    def _extra_payload(self) -> dict:
        return {"thinking": {"type": self.thinking}} if self.thinking is not None else {}
