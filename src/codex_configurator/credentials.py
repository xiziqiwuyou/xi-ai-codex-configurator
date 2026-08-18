from __future__ import annotations

import getpass
from collections.abc import Callable

from .errors import CredentialError


def prompt_token(
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
) -> str:
    input_fn("Press Enter to enter the Xi-AI API token...")
    token = secret_fn("Xi-AI API token: ").strip()
    if not token:
        raise CredentialError("The API token cannot be empty")
    return token
