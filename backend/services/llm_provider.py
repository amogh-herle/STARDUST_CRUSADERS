"""
Provider abstraction for LLM backends.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

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
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "Google Generative AI SDK is required. "
                "Install with: pip install google-generativeai"
            ) from exc

        genai.configure(api_key=self.api_key)

        # Separate system prompt from user messages
        system_message = None
        user_parts: List[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_message = content
            elif role == "user":
                user_parts.append(content)
            elif role == "assistant":
                user_parts.append(f"Assistant: {content}")

        # Build the full prompt — system instruction prepended
        prompt = "\n\n".join(user_parts)
        if system_message:
            prompt = f"{system_message}\n\n{prompt}"

        gemini_model = genai.GenerativeModel(model)
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
        )

        return response.text.strip()


@dataclass
class AnthropicProvider(LLMProvider):
    api_key: str

    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
    ) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "Anthropic SDK is required. Install with: pip install anthropic"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)

        system_message = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append({"role": msg["role"], "content": msg["content"]})

        response = client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            temperature=temperature,
            system=system_message,
            messages=chat_messages,
        )
        return response.content[0].text.strip()


class ProviderFactory:
    @staticmethod
    def create_provider() -> LLMProvider:
        provider = settings.LLM_PROVIDER.lower()
        if provider == "gemini":
            return GeminiProvider(api_key=settings.GEMINI_API_KEY)
        elif provider in ("anthropic", "claude"):
            api_key = getattr(settings, "ANTHROPIC_API_KEY", "") or getattr(settings, "CLAUDE_API_KEY", "")
            return AnthropicProvider(api_key=api_key)
        raise ValueError(f"Unsupported LLM provider: {settings.LLM_PROVIDER!r}")
