"""
Provider abstraction for LLM backends.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from config import settings


class LLMProvider:
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
    ) -> str:
        raise NotImplementedError()


@dataclass
class GeminiProvider(LLMProvider):
    api_key: str

    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
    ) -> str:
        try:
            import google.generativeai as generativeai
        except ImportError as exc:
            raise ImportError(
                "Google Generative AI SDK is required for Gemini provider. "
                "Install google-ai-generative in the project environment."
            ) from exc

        generativeai.configure(api_key=self.api_key)
        response = generativeai.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        if hasattr(response, "last") and response.last is not None:
            return response.last

        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            if hasattr(choice, "message") and choice.message:
                message = choice.message
                if isinstance(message, dict):
                    return message.get("content", "").strip()
                return str(message)
            if hasattr(choice, "content"):
                return str(choice.content).strip()

        return str(response)


class ProviderFactory:
    @staticmethod
    def create_provider() -> LLMProvider:
        provider = settings.LLM_PROVIDER.lower()
        if provider == "gemini":
            return GeminiProvider(api_key=settings.GEMINI_API_KEY)
        raise ValueError(f"Unsupported LLM provider: {settings.LLM_PROVIDER}")
