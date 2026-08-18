import os
import tempfile
import unittest
from pathlib import Path

from codex_configurator.discovery import resolve_codex_home
from codex_configurator.endpoints import API_BASE, MODELS_URL, RESPONSES_URL, api_base_from_origin, resource_url


class EndpointTests(unittest.TestCase):
    def test_fixed_responses_paths(self):
        self.assertEqual(API_BASE, "https://api.xi-ai.cn/v1")
        self.assertEqual(MODELS_URL, "https://api.xi-ai.cn/v1/models")
        self.assertEqual(RESPONSES_URL, "https://api.xi-ai.cn/v1/responses")
        self.assertEqual(api_base_from_origin("https://api.xi-ai.cn/v1/"), API_BASE)
        self.assertEqual(resource_url("responses"), RESPONSES_URL)
        self.assertNotIn("/v1/v1", RESPONSES_URL)


class DiscoveryTests(unittest.TestCase):
    def test_explicit_home_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            expected = Path(temp).resolve()
            actual = resolve_codex_home(temp, env={"CODEX_HOME": "ignored"})
            self.assertEqual(actual, expected)

    def test_environment_home_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            actual = resolve_codex_home(env={"CODEX_HOME": temp})
            self.assertEqual(actual, Path(temp).resolve())

    def test_default_home(self):
        with tempfile.TemporaryDirectory() as temp:
            actual = resolve_codex_home(env={}, home=Path(temp))
            self.assertEqual(actual, (Path(temp) / ".codex").resolve())


if __name__ == "__main__":
    unittest.main()
