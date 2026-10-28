# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ollama's public AI route and asynchronous installed-model cache."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from slidemage.plugin_api import Host, Route, Widget

from .tasks import CHAT, IMAGE_DESCRIPTION, TASK_LABELS, TASKS
from .provider import InstalledModel, OllamaProvider, ollama_host


def _missing_model(task: str) -> str:
    return (f"Choose an Ollama model for {TASK_LABELS[task].lower()} under "
            "AI setup (the ▾ of the AI switch).")


class OllamaRoute:
    id = "ollama"
    service = "Ollama"
    is_local = True
    tasks = frozenset(TASKS)

    def __init__(self, host: Host) -> None:
        self.host = host
        self._installed: list[InstalledModel] = []
        self._models_at: str | None = None
        self._loading_at: str | None = None
        self._generation = 0

    def problem(self, ctx: Any, route: Route) -> str:
        return "" if route.model else _missing_model(route.task)

    def default_choice(self, task: str) -> str:
        return ""

    def choice_label(self, choice: str) -> str:
        return choice

    def split_choice(self, choice: str) -> tuple[str, str | None]:
        return choice, None

    def valid_choice(self, ctx: Any, choice: str) -> bool:
        return bool(choice)

    def work_label(self, route: Route) -> str:
        return f"{route.model or 'no model chosen'} · Ollama"

    def model_choices(self, ctx: Any, task: str) -> list[str]:
        """Return immediately. Only refresh_models' worker contacts Ollama."""
        address = ollama_host(self.host)
        if self._models_at != address:
            if self._loading_at != address:
                self.refresh_models()
            return []
        return [model.name for model in self._installed
                if (task != CHAT or model.supports_tools)
                and (task != IMAGE_DESCRIPTION or "vision" in model.capabilities)]

    def refresh_models(self, done: Callable[[list[InstalledModel]], None] | None = None) -> None:
        """Refresh the cache off-thread; ignore superseded requests and old addresses.

        The host has no model-list change notification. Core can ask
        model_choices again after completion; Settings consumes the callback.
        """
        address = ollama_host(self.host)
        provider = OllamaProvider(host=address)
        self._generation += 1
        generation = self._generation
        self._loading_at = address

        def finished(models: list[InstalledModel] | None, error: BaseException | None) -> None:
            if generation != self._generation:
                return
            self._loading_at = None
            if address != ollama_host(self.host):
                return
            self._models_at = address
            self._installed = list(models or [])
            if error is not None:
                self.host.log(f"Could not list Ollama models: {error}")
            if done is not None:
                done(list(self._installed))

        try:
            self.host.run_in_background(lambda: provider.installed(detailed=True), finished)
        except Exception as exc:  # scheduling failed before the work started
            finished(None, exc)

    def provider(self, ctx: Any, route: Route) -> OllamaProvider:
        return OllamaProvider.for_host(self.host, route.model)

    def draft_backend(self, ctx: Any, route: Route, cancel: Any = None) -> None:
        return None  # core's image backend wraps provider()

    def translation_backend(self, ctx: Any, route: Route, cancel: Any = None) -> None:
        return None  # core's translation backend wraps provider()

    def slide_translator(self, ctx: Any, route: Route) -> None:
        return None  # core wraps provider()

    def task_runner(self, ctx: Any, route: Route) -> None:
        return None  # core's provider_tasks

    def chat_surface(self, *args: Any, **kwargs: Any) -> None:
        return None  # core's provider chat view

    def settings_section(self) -> None:
        # Registered through Host.add_settings_section, so core must not build it twice.
        return None

    def build_settings_section(self) -> Widget:
        from .settings import OllamaTab  # Qt only when Settings opens

        return OllamaTab(self.host, self).page
