import inspect
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from codex_configurator.catalog import merge_catalog, validate_catalog
from codex_configurator.toml_merge import (
    CLEAR_CONTEXT,
    CONTEXT_1M,
    CONTEXT_500K,
    merge_config,
)


def bundled_catalog():
    return {
        "models": [
            {
                "slug": "gpt-bundled",
                "display_name": "Bundled",
                "description": "Bundled model",
                "default_reasoning_level": "low",
                "supported_reasoning_levels": [{"effort": "low", "description": "Fast"}],
                "priority": 1,
                "visibility": "list",
                "supported_in_api": True,
                "context_window": 200000,
                "max_context_window": 200000,
            }
        ]
    }


class CatalogTests(unittest.TestCase):
    def test_bundled_models_are_preserved_and_remote_models_appended(self):
        merged = merge_catalog(bundled_catalog(), ["gpt-bundled", "remote-a", "remote-a"])
        self.assertEqual([item["slug"] for item in merged["models"]], ["gpt-bundled", "remote-a"])
        remote = merged["models"][1]
        self.assertEqual(remote["context_window"], 128000)
        self.assertEqual(remote["input_modalities"], ["text"])

    def test_duplicate_catalog_slugs_are_rejected(self):
        with self.assertRaises(Exception):
            validate_catalog({"models": [{"slug": "a"}, {"slug": "a"}]})


class TomlMergeTests(unittest.TestCase):
    def test_unrelated_settings_survive(self):
        existing = """# user comment
sandbox_mode = "workspace-write"
model = "old"
preferred_auth_method = "apikey"

[mcp_servers.demo]
command = "demo"

[model_providers.xi_ai]
name = "old"
base_url = "https://old.invalid/v1"

[model_providers.xi_ai.http_headers]
X-Test = "remove-me"

[projects."C:/repo"]
trust_level = "trusted"
"""
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "catalog.json"
            result = merge_config(existing, model="remote-a", catalog_path=catalog, token="secret")
        parsed = tomllib.loads(result)
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertEqual(parsed["mcp_servers"]["demo"]["command"], "demo")
        self.assertEqual(parsed["projects"]["C:/repo"]["trust_level"], "trusted")
        self.assertEqual(parsed["model"], "remote-a")
        self.assertEqual(parsed["model_provider"], "xi_ai")
        self.assertNotIn("preferred_auth_method", parsed)
        self.assertEqual(parsed["forced_login_method"], "api")
        self.assertEqual(
            parsed["model_providers"]["xi_ai"]["base_url"],
            "https://api.xi-ai.net/v1",
        )
        self.assertNotIn("http_headers", parsed["model_providers"]["xi_ai"])
        self.assertEqual(result.count("[model_providers.xi_ai]"), 1)

    def test_context_presets_write_top_level_integer_values(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "catalog.json"
            for context, expected in (
                (CONTEXT_500K, (500_000, 450_000)),
                (CONTEXT_1M, (1_000_000, 900_000)),
            ):
                parsed = tomllib.loads(
                    merge_config(
                        "",
                        model="gpt-5.6-sol",
                        catalog_path=catalog,
                        token="secret",
                        context=context,
                    )
                )
                self.assertEqual(parsed["model_context_window"], expected[0])
                self.assertEqual(
                    parsed["model_auto_compact_token_limit"], expected[1]
                )

    def test_context_preserve_and_clear_are_distinct(self):
        existing = """model_context_window = 640000
model_auto_compact_token_limit = 600000
custom_setting = "keep"
"""
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "catalog.json"
            preserved = tomllib.loads(
                merge_config(
                    existing,
                    model="gpt-5.6-sol",
                    catalog_path=catalog,
                    token="secret",
                )
            )
            cleared = tomllib.loads(
                merge_config(
                    existing,
                    model="gpt-5.6-sol",
                    catalog_path=catalog,
                    token="secret",
                    context=CLEAR_CONTEXT,
                )
            )
        self.assertEqual(preserved["model_context_window"], 640000)
        self.assertEqual(preserved["model_auto_compact_token_limit"], 600000)
        self.assertNotIn("model_context_window", cleared)
        self.assertNotIn("model_auto_compact_token_limit", cleared)
        self.assertEqual(cleared["custom_setting"], "keep")

    def test_context_parameter_uses_explicit_preserve_operation(self):
        parameter = inspect.signature(merge_config).parameters["context"]

        self.assertEqual(parameter.annotation, "ContextConfig")
        self.assertEqual(parameter.default.mode, "preserve")


if __name__ == "__main__":
    unittest.main()
