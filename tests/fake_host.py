# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fake public host with recorded registrations, persisted settings and queued workers.

finish_next runs work on a real worker and done on the test/UI thread. Qt is only
imported by widget factories, so activation tests need no Qt.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident
from typing import Any, Callable, Literal, TYPE_CHECKING

from slidemage.plugin_api import (Credentials, Deck, EditWatch, ExternalEdits, Focus, FooterAction,
                                  InsertPosition, TerminalService, Widget)

if TYPE_CHECKING:
    from slidemage.plugin_api import ToolBox


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
    def __init__(self, *, settings: FakeSettings | None = None) -> None:
        self.store = settings if settings is not None else FakeSettings()
        self.routes: dict[str, Any] = {}
        self.sections: list[Section] = []
        self.pending: list[tuple[Callable, Callable]] = []
        self.logs: list[str] = []
        self.messages: list[tuple[str, bool]] = []
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
        if page != "ai":
            raise ValueError(f"unknown settings page {page!r}")
        self.sections.append(Section(page, title, build))

    def run_in_background(self, work, done) -> None:
        self._check_ui()
        if self.background_error is not None:
            raise self.background_error
        self.pending.append((work, done))

    def log(self, message) -> None:
        self._check_ui()
        self.logs.append(message)

    def show_message(self, text, error=False) -> None:
        self._check_ui()
        self.messages.append((text, error))

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
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self.caption(label))
        layout.addWidget(widget)
        return container

    def caption(self, text, tone=""):
        from PySide6.QtWidgets import QLabel

        return QLabel(text)

    # Unused Host services fail loudly if a test unexpectedly calls them.

    def cache_dir(self) -> Path:
        raise NotImplementedError('FakeHost.cache_dir')

    def legacy_cache_dir(self) -> Path | None:
        raise NotImplementedError('FakeHost.legacy_cache_dir')

    def add_command(self, id: str, title: str, run: Callable[[Deck], None], enabled: Callable[[Deck | None], bool]=lambda deck: True) -> None:
        raise NotImplementedError('FakeHost.add_command')

    def add_menu_entry(self, menu: Literal['deck'], command_id: str) -> None:
        raise NotImplementedError('FakeHost.add_menu_entry')

    def add_deck_settings_tab(self, title: str, build: Callable[[Deck], Widget], footer_action: FooterAction | None=None) -> None:
        raise NotImplementedError('FakeHost.add_deck_settings_tab')

    def add_settings_tab(self, title: str, build: Callable[[], Widget]) -> None:
        raise NotImplementedError('FakeHost.add_settings_tab')

    def add_sidebar_box(self, key: str, title: str, build: Callable[[Deck], Widget], size: Literal['compact', 'large']='compact', when: Callable[[Deck], bool]=lambda deck: True) -> None:
        raise NotImplementedError('FakeHost.add_sidebar_box')

    def add_render_priority(self, soon: Callable[[Deck], bool]) -> None:
        raise NotImplementedError('FakeHost.add_render_priority')

    def add_entry_point(self, name: str, fn: Callable[[list[str]], int]) -> None:
        raise NotImplementedError('FakeHost.add_entry_point')

    def entry_point_command(self, name: str) -> list[str]:
        raise NotImplementedError('FakeHost.entry_point_command')

    def terminal(self) -> TerminalService:
        raise NotImplementedError('FakeHost.terminal')

    def watch_external_edits(self, on_change: Callable[[ExternalEdits], None]) -> EditWatch:
        raise NotImplementedError('FakeHost.watch_external_edits')

    def credentials(self, env_var: str | None=None) -> Credentials:
        raise NotImplementedError('FakeHost.credentials')

    def ai_tools(self, scope: Literal['slide', 'deck']='slide', *, focus_slide: Path | None=None, mode: Literal['ask', 'auto']='auto', cancel: Any=None, workspace: Any=None) -> 'ToolBox':
        raise NotImplementedError('FakeHost.ai_tools')

    def focus(self) -> Focus:
        raise NotImplementedError('FakeHost.focus')

    def insert_positions(self, deck: Deck) -> list[InsertPosition]:
        raise NotImplementedError('FakeHost.insert_positions')

    def insert_slides(self, deck: Deck, position: InsertPosition, slides: list[tuple[str, str]], language: Literal['de', 'en']) -> list[Path]:
        raise NotImplementedError('FakeHost.insert_slides')

    def edit_deck_text(self, deck: Deck, change: Callable[[str], str]) -> str:
        raise NotImplementedError('FakeHost.edit_deck_text')

    def deck_language(self, deck: Deck) -> Literal['de', 'en']:
        raise NotImplementedError('FakeHost.deck_language')

    def open_url(self, url: str) -> None:
        raise NotImplementedError('FakeHost.open_url')

    def open_deck_settings(self, deck: Deck, tab: str | None=None) -> None:
        raise NotImplementedError('FakeHost.open_deck_settings')
