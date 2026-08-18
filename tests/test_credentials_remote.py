import io
import json
import unittest
from urllib.error import HTTPError

from codex_configurator.credentials import prompt_token
from codex_configurator.errors import CredentialError, RemoteModelError
from codex_configurator.remote_models import fetch_remote_model_ids


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class CredentialTests(unittest.TestCase):
    def test_prompts_for_secret_exactly_once(self):
        enter_prompts = []
        secret_prompts = []

        token = prompt_token(
            input_fn=lambda prompt: enter_prompts.append(prompt) or "",
            secret_fn=lambda prompt: secret_prompts.append(prompt) or "secret-token",
        )

        self.assertEqual(token, "secret-token")
        self.assertEqual(len(enter_prompts), 1)
        self.assertEqual(len(secret_prompts), 1)

    def test_empty_secret_is_rejected(self):
        with self.assertRaises(CredentialError):
            prompt_token(input_fn=lambda _: "", secret_fn=lambda _: "   ")


class RemoteModelTests(unittest.TestCase):
    def test_model_ids_are_validated_and_deduplicated(self):
        payload = json.dumps(
            {"data": [{"id": "alpha"}, {"id": "alpha"}, {"id": " beta "}, {}]}
        ).encode()
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            return FakeResponse(payload)

        models = fetch_remote_model_ids("top-secret", opener=opener)

        self.assertEqual(models, ["alpha", "beta"])
        self.assertEqual(captured["url"], "https://api.xi-ai.cn/v1/models")
        self.assertEqual(captured["authorization"], "Bearer top-secret")

    def test_authentication_error_is_secret_free(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO())

        with self.assertRaisesRegex(RemoteModelError, "rejected") as context:
            fetch_remote_model_ids("top-secret", opener=opener)
        self.assertNotIn("top-secret", str(context.exception))

    def test_invalid_shape_is_rejected(self):
        with self.assertRaises(RemoteModelError):
            fetch_remote_model_ids(
                "token", opener=lambda request, timeout: FakeResponse(b'{"models": []}')
            )


if __name__ == "__main__":
    unittest.main()
