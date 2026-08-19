from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TextIO


ProgressState = Literal["start", "update", "complete"]


@dataclass(frozen=True)
class ProgressEvent:
    phase: str
    label: str
    state: ProgressState
    current: int | None = None
    total: int | None = None

    @property
    def status(self) -> ProgressState:
        return self.state


ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    label: str,
    state: ProgressState,
    *,
    current: int | None = None,
    total: int | None = None,
) -> None:
    if callback is None:
        return
    try:
        callback(ProgressEvent(phase, label, state, current, total))
    except Exception:
        # Progress is observational and must not change transaction behavior.
        pass


class ConsoleProgress:
    def __init__(
        self,
        *,
        output: Callable[[str], None] = print,
        stream: TextIO | None = None,
        tty: bool | None = None,
        percent_step: int = 5,
    ) -> None:
        self._output = output
        self._stream = stream if stream is not None else sys.stdout
        self._tty = (
            self._stream.isatty()
            if tty is None and output is print
            else bool(tty)
        )
        self._percent_step = max(1, min(percent_step, 100))
        self._last_bucket: dict[str, int] = {}
        self._last_unknown: dict[str, int] = {}
        self._line_width = 0

    def _counts(self, event: ProgressEvent) -> str:
        if event.current is None:
            return ""
        if event.total is None:
            return f"（已处理 {event.current}）"
        if event.total == 0:
            return "（0/0）"
        percent = min(100, int(event.current * 100 / event.total))
        bar = ""
        if self._tty:
            filled = min(20, int(percent * 20 / 100))
            bar = f" [{'#' * filled}{'-' * (20 - filled)}]"
        return f"{bar} {percent:3d}%（{event.current}/{event.total}）"

    def _render(self, event: ProgressEvent) -> str:
        if event.state == "start":
            return f"[{event.label}] 开始{self._counts(event)}"
        if event.state == "complete":
            return f"[{event.label}] 完成{self._counts(event)}"
        return f"[{event.label}]{self._counts(event)}"

    def _should_emit_update(self, event: ProgressEvent) -> bool:
        if event.current is None:
            return False
        if event.total is not None:
            if event.total <= 0:
                return False
            percent = min(100, int(event.current * 100 / event.total))
            bucket = percent // self._percent_step
            if self._last_bucket.get(event.phase) == bucket:
                return False
            self._last_bucket[event.phase] = bucket
            return True
        previous = self._last_unknown.get(event.phase, -1_048_576)
        if event.current - previous < 1_048_576:
            return False
        self._last_unknown[event.phase] = event.current
        return True

    def __call__(self, event: ProgressEvent) -> None:
        if event.state == "start":
            self._last_bucket.pop(event.phase, None)
            self._last_unknown.pop(event.phase, None)
        if (
            event.state == "update"
            and not self._tty
            and not self._should_emit_update(event)
        ):
            return
        rendered = self._render(event)
        if self._tty:
            padding = " " * max(0, self._line_width - len(rendered))
            self._stream.write(f"\r{rendered}{padding}")
            self._stream.flush()
            self._line_width = len(rendered)
            if event.state == "complete":
                self._stream.write("\n")
                self._stream.flush()
                self._line_width = 0
        else:
            self._output(rendered)
        if event.state == "complete":
            self._last_bucket.pop(event.phase, None)
            self._last_unknown.pop(event.phase, None)
