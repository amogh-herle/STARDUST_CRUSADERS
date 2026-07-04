import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.llm_provider import ProviderFactory, GeminiProvider
import config


class TestLLMProvider(unittest.TestCase):
    def test_create_gemini_provider(self):
        provider = ProviderFactory.create_provider()
        self.assertIsInstance(provider, GeminiProvider)

    def test_unsupported_provider_raises(self):
        original = config.settings.LLM_PROVIDER
        config.settings.LLM_PROVIDER = "unsupported"
        try:
            with self.assertRaises(ValueError):
                ProviderFactory.create_provider()
        finally:
            config.settings.LLM_PROVIDER = original
