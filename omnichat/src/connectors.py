# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T11:00:00Z
# Modified at: 2026-01-26T12:00:00Z
# Purpose: AI Agent connectors for HOPE OMNI-CHAT
# Security: Secrets masking implemented
# === END SIGNATURE ===
"""
AI Agent Connectors - Async wrappers for Gemini, GPT, and Claude APIs.

Each agent tracks token usage for cost estimation.
Fail-safe: if one agent fails, others still respond.

SECURITY: All API keys are masked in logs and error messages.
"""

from __future__ import annotations

import os
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load from multiple .env files (local first, then main secrets)
_local_env = Path(__file__).parent.parent / ".env"
_main_secrets = Path(r"C:\secrets\hope\.env")

if _local_env.exists():
    load_dotenv(_local_env)
if _main_secrets.exists():
    load_dotenv(_main_secrets, override=False)  # Don't override local values


# === SECURITY: USE CENTRALIZED REDACTION ===
# Import from security.py - single source of truth for secret masking
from .security import redact

# Alias for backward compatibility
mask_secret = redact

_log = logging.getLogger("omnichat.connectors")


@dataclass
class TokenUsage:
    """Track token usage and estimate costs."""
    input_tokens: int = 0
    output_tokens: int = 0

    # Pricing per 1M tokens (approximate, USD)
    INPUT_COST_PER_M: float = 0.0
    OUTPUT_COST_PER_M: float = 0.0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    @property
    def total_cost_usd(self) -> float:
        input_cost = (self.input_tokens / 1_000_000) * self.INPUT_COST_PER_M
        output_cost = (self.output_tokens / 1_000_000) * self.OUTPUT_COST_PER_M
        return input_cost + output_cost

    @property
    def total_cost_cents(self) -> float:
        return self.total_cost_usd * 100


@dataclass
class GeminiTokenUsage(TokenUsage):
    """Gemini 1.5 Flash pricing."""
    INPUT_COST_PER_M: float = 0.075  # $0.075 per 1M input tokens
    OUTPUT_COST_PER_M: float = 0.30   # $0.30 per 1M output tokens


@dataclass
class GPTTokenUsage(TokenUsage):
    """GPT-4o pricing."""
    INPUT_COST_PER_M: float = 2.50   # $2.50 per 1M input tokens
    OUTPUT_COST_PER_M: float = 10.00  # $10.00 per 1M output tokens


@dataclass
class ClaudeTokenUsage(TokenUsage):
    """Claude 3.5 Sonnet pricing."""
    INPUT_COST_PER_M: float = 3.00   # $3.00 per 1M input tokens
    OUTPUT_COST_PER_M: float = 15.00  # $15.00 per 1M output tokens


class BaseAgent(ABC):
    """Abstract base class for AI agents."""

    name: str = "Agent"
    color: str = "white"
    is_connected: bool = False
    error_message: Optional[str] = None

    @abstractmethod
    async def ask_async(self, text: str) -> str:
        """Send message and get response asynchronously."""
        pass

    @abstractmethod
    def get_usage(self) -> TokenUsage:
        """Get current token usage."""
        pass

    async def health_check(self) -> bool:
        """Check if agent is available."""
        try:
            response = await asyncio.wait_for(
                self.ask_async("Say 'OK' if you're working."),
                timeout=10.0
            )
            self.is_connected = "OK" in response.upper() or len(response) > 0
            self.error_message = None
            return self.is_connected
        except Exception as e:
            self.is_connected = False
            self.error_message = str(e)
            return False


class GeminiAgent(BaseAgent):
    """Gemini AI Agent - Chief Architect & Strategist."""

    name = "Gemini"
    color = "magenta"

    # TUNED SYSTEM PROMPT (by Gemini Architect)
    SYSTEM_PROMPT = (
        "Ты — Gemini 1.5, Главный Архитектор и Стратег системы HOPE.\n"
        "Твои задачи:\n"
        "1. Оценивать риски предложений GPT и кода Claude.\n"
        "2. Следить за безопасностью (Security First).\n"
        "3. Предлагать масштабируемые архитектурные решения.\n"
        "4. НЕ писать код реализации (это делает Claude), а писать алгоритмы и концепции.\n"
        "Отвечай структурно, используй списки. Будь мудрым наставником.\n"
        "Отвечай на русском языке."
    )

    def __init__(self):
        self.usage = GeminiTokenUsage()
        self.chat_session = None
        self.model = None

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            self.is_connected = False
            self.error_message = "GEMINI_API_KEY not found in .env"
            return

        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(
                'gemini-1.5-flash-latest',
                system_instruction=self.SYSTEM_PROMPT
            )
            self.chat_session = self.model.start_chat(history=[])
            self.is_connected = True
        except Exception as e:
            self.is_connected = False
            self.error_message = str(e)

    async def ask_async(self, text: str) -> str:
        if not self.is_connected or not self.chat_session:
            return f"❌ Gemini недоступен: {self.error_message}"

        try:
            response = await self.chat_session.send_message_async(text)

            # Track token usage
            if hasattr(response, 'usage_metadata'):
                self.usage.add(
                    response.usage_metadata.prompt_token_count or 0,
                    response.usage_metadata.candidates_token_count or 0
                )

            return response.text
        except Exception as e:
            # SECURITY: mask any secrets in error message
            safe_error = mask_secret(str(e))
            self.error_message = safe_error
            _log.error(f"Gemini error: {safe_error}")
            return f"❌ Ошибка Gemini: {safe_error}"

    def get_usage(self) -> TokenUsage:
        return self.usage


class GPTAgent(BaseAgent):
    """GPT AI Agent - Senior Developer & Analyst."""

    name = "GPT"
    color = "yellow"

    # TUNED SYSTEM PROMPT
    SYSTEM_PROMPT = (
        "Ты — GPT, Senior Python Developer и Аналитик проекта HOPE.\n"
        "Твои задачи:\n"
        "1. Писать чистый, документированный Python код.\n"
        "2. Искать баги и уязвимости в коде.\n"
        "3. Анализировать данные и формулировать ТЗ.\n"
        "4. Проводить code review решений Claude.\n"
        "Пиши код блоками с комментариями. Используй type hints.\n"
        "Отвечай на русском языке."
    )

    def __init__(self):
        self.usage = GPTTokenUsage()
        self.client = None
        self.messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self.is_connected = False
            self.error_message = "OPENAI_API_KEY not found in .env"
            return

        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(api_key=api_key)
            self.is_connected = True
        except Exception as e:
            self.is_connected = False
            self.error_message = str(e)

    async def ask_async(self, text: str) -> str:
        if not self.is_connected or not self.client:
            return f"❌ GPT недоступен: {self.error_message}"

        try:
            self.messages.append({"role": "user", "content": text})

            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=self.messages,
                max_tokens=2048,
            )

            assistant_message = response.choices[0].message.content
            self.messages.append({"role": "assistant", "content": assistant_message})

            # Track token usage
            if response.usage:
                self.usage.add(
                    response.usage.prompt_tokens or 0,
                    response.usage.completion_tokens or 0
                )

            return assistant_message
        except Exception as e:
            # SECURITY: mask any secrets in error message
            safe_error = mask_secret(str(e))
            self.error_message = safe_error
            _log.error(f"GPT error: {safe_error}")
            return f"❌ Ошибка GPT: {safe_error}"

    def get_usage(self) -> TokenUsage:
        return self.usage


class ClaudeAgent(BaseAgent):
    """Claude AI Agent - Lead Engineer & Implementor."""

    name = "Claude"
    color = "cyan"

    # TUNED SYSTEM PROMPT
    SYSTEM_PROMPT = (
        "Ты — Claude, Lead Engineer и главный исполнитель проекта HOPE.\n"
        "Твои задачи:\n"
        "1. Реализовывать архитектурные решения Gemini.\n"
        "2. Выполнять ТЗ от GPT.\n"
        "3. Писать production-ready код с тестами.\n"
        "4. Рефакторинг и CI/CD.\n"
        "5. Fail-closed подход: при сомнениях — останавливаться и спрашивать.\n"
        "Код должен быть рабочим, не заглушками. Документируй решения.\n"
        "Отвечай на русском языке."
    )

    def __init__(self):
        self.usage = ClaudeTokenUsage()
        self.client = None
        self.messages = []

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            self.is_connected = False
            self.error_message = "ANTHROPIC_API_KEY not found in .env"
            return

        try:
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=api_key)
            self.is_connected = True
        except Exception as e:
            self.is_connected = False
            self.error_message = str(e)

    async def ask_async(self, text: str) -> str:
        if not self.is_connected or not self.client:
            return f"❌ Claude недоступен: {self.error_message}"

        try:
            self.messages.append({"role": "user", "content": text})

            response = await self.client.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=2048,
                system=self.SYSTEM_PROMPT,
                messages=self.messages,
            )

            assistant_message = response.content[0].text
            self.messages.append({"role": "assistant", "content": assistant_message})

            # Track token usage
            if response.usage:
                self.usage.add(
                    response.usage.input_tokens or 0,
                    response.usage.output_tokens or 0
                )

            return assistant_message
        except Exception as e:
            # SECURITY: mask any secrets in error message
            safe_error = mask_secret(str(e))
            self.error_message = safe_error
            _log.error(f"Claude error: {safe_error}")
            return f"❌ Ошибка Claude: {safe_error}"

    def get_usage(self) -> TokenUsage:
        return self.usage


# Factory function
def create_all_agents() -> dict[str, BaseAgent]:
    """Create all available agents."""
    return {
        "gemini": GeminiAgent(),
        "gpt": GPTAgent(),
        "claude": ClaudeAgent(),
    }
