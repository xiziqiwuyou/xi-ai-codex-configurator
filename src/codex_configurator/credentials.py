from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from typing import TextIO

from .errors import CredentialError


def _read_masked_chars(
    read_char: Callable[[], str], write: Callable[[str], object]
) -> str:
    characters: list[str] = []
    while True:
        character = read_char()
        if not character:
            raise EOFError
        if character in {"\r", "\n"}:
            write("\n")
            return "".join(characters)
        if character == "\x03":
            raise KeyboardInterrupt
        if character == "\x1a":
            raise EOFError
        if character in {"\x00", "\xe0"}:
            read_char()
            continue
        if character in {"\b", "\x7f"}:
            if characters:
                characters.pop()
                write("\b \b")
            continue
        if character.isprintable():
            characters.append(character)
            write("*")


def _terminal_writer(stream: TextIO) -> Callable[[str], object]:
    def write(value: str) -> object:
        result = stream.write(value)
        stream.flush()
        return result

    return write


def _read_masked_windows(prompt: str, output_stream: TextIO) -> str:
    import msvcrt

    write = _terminal_writer(output_stream)
    write(prompt)
    try:
        return _read_masked_chars(msvcrt.getwch, write)
    except (EOFError, KeyboardInterrupt):
        write("\n")
        raise


def _read_masked_posix(
    prompt: str, input_stream: TextIO, output_stream: TextIO
) -> str:
    import termios

    if not input_stream.isatty():
        return getpass.getpass(prompt)
    descriptor = input_stream.fileno()
    original = termios.tcgetattr(descriptor)
    masked = original.copy()
    masked[6] = original[6].copy()
    masked[3] &= ~(termios.ECHO | termios.ICANON)
    masked[6][termios.VMIN] = 1
    masked[6][termios.VTIME] = 0
    write = _terminal_writer(output_stream)
    write(prompt)
    termios.tcsetattr(descriptor, termios.TCSADRAIN, masked)
    try:
        return _read_masked_chars(lambda: input_stream.read(1), write)
    except (EOFError, KeyboardInterrupt):
        write("\n")
        raise
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)


def read_masked_secret(prompt: str) -> str:
    try:
        if sys.platform.startswith("win"):
            return _read_masked_windows(prompt, sys.stderr)
        return _read_masked_posix(prompt, sys.stdin, sys.stderr)
    except (ImportError, OSError, AttributeError):
        return getpass.getpass(prompt)


def prompt_token(
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = read_masked_secret,
) -> str:
    input_fn("按 Enter 键后输入 Xi-AI API Key（输入时仅显示星号）...")
    token = secret_fn("Xi-AI API Key：").strip()
    if not token:
        raise CredentialError("API Key 不能为空")
    return token
