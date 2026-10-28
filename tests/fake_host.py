# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draft host with recorded registrations, persisted settings and queued workers.

finish_next runs work on a real worker and done on the test/UI thread. Qt is only
imported by widget factories, so activation tests need neither Qt nor SlideMage.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import get_ident
from typing import Any, Callable


class FakeSettings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = deepcopy(values or {})
        self.saved = deepcopy(self.values)
        self.save_count = 0
        self.save_error: OSError | None = None

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value) -> None:
        self.values[key] = deepcopy(value)

    def save(self) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved = deepcopy(self.values)
        self.save_count += 1


@dataclass
class Section:
    page: str
    title: str
    build: Callable[[], Any]


class FakeHost:
    def __init__(self, *, settings: FakeSettings | None = None, legacy: dict | None = None) -> None:
        self.store = settings if settings is not None else FakeSettings()
        self.legacy = deepcopy(legacy or {})
        self.legacy_reads: list[str] = []
        self.routes: dict[str, Any] = {}
        self.sections: list[Section] = []
        self.pending: list[tuple[Callable, Callable]] = []
        self.logs: list[str] = []
        self.messages: list[tuple[str, bool]] = []
        self.task_attempts: list[str] = []
        self.task: str | None = None
        self.task_ends = 0
        self.background_error: Exception | None = None
        self._active = True
        self._ui_thread = get_ident()

    def _check_ui(self) -> None:
        assert get_ident() == self._ui_thread, "host/UI call from worker"

    def finish_activation(self) -> None:
        self._active = False

    def finish_next(self, index: int = 0) -> None:
        work, done = self.pending.pop(index)
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                result = executor.submit(work).result(timeout=5)
            except BaseException as exc:
                done(None, exc)
            else:
                done(result, None)

    def settings(self) -> FakeSettings:
        self._check_ui()
        return self.store

    def legacy_setting(self, key):
        self._check_ui()
        self.legacy_reads.append(key)
        return deepcopy(self.legacy.get(key))

    def add_ai_route(self, route) -> None:
        self._check_ui()
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        if route.id in self.routes:
            raise ValueError(f"duplicate route: {route.id}")
        self.routes[route.id] = route

    def add_settings_section(self, page, title, build) -> None:
        self._check_ui()
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        self.sections.append(Section(page, title, build))

    def run_in_background(self, work, done) -> None:
        self._check_ui()
        if self.background_error is not None:
            raise self.background_error
        self.pending.append((work, done))

    def log(self, message) -> None:
        self.logs.append(message)  # progress logging also works from workers

    def show_message(self, text, error=False) -> None:
        self._check_ui()
        self.messages.append((text, error))

    def try_start_ollama_task(self, what) -> bool:
        self._check_ui()
        self.task_attempts.append(what)
        if self.task is not None:
            self.show_message(f"Ollama is busy: {self.task}", error=True)
            return False
        self.task = what
        return True

    def end_ollama_task(self) -> None:
        self._check_ui()
        assert self.task is not None, "the slot must be released exactly once"
        self.task = None
        self.task_ends += 1

    def Page(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        class Page(QWidget):
            changed = Signal()

            def __init__(self):
                super().__init__()
                self.body = QVBoxLayout(self)

        return Page()

    def Field(self, label, widget):
        return self.row(self.caption(label), widget)

    def caption(self, text, tone=""):
        from PySide6.QtWidgets import QLabel

        return QLabel(text)

    def Button(self, text, *, compact=False):
        from PySide6.QtWidgets import QPushButton

        return QPushButton(text)

    def row(self, *widgets):
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        container = QWidget()
        layout = QHBoxLayout(container)
        for widget in widgets:
            layout.addWidget(widget)
        return container
