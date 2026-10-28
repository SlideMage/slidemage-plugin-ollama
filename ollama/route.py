# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""DRAFT Ollama route for the planned AiRoute contract (host.py).

The plugin's migrated per-task models supply defaults; an explicit resolved model
always wins. Model pickers read a cache, filled on a host worker on their first
query or on Refresh in Settings. There is no request during activation.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .host import CHAT, IMAGE_DESCRIPTION, TASK_LABELS, TASKS, Host, ProviderError, ResolvedRoute, Widget
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

    def problem(self, ctx: Any, task: str) -> str:
        """Local resolver wording; do not probe the server or pick a fallback."""
        return "" if self.default_choice(task) else _missing_model(task)

    def default_choice(self, task: str) -> str:
        models = self.host.settings().get("models")
        choice = models.get(task) if isinstance(models, dict) else None
        return choice if isinstance(choice, str) else ""

    def choice_label(self, choice: str) -> str:
        return choice

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

        DRAFT: the plan has no model-list change notification yet. Core can ask
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

    def provider(self, ctx: Any, route: ResolvedRoute) -> OllamaProvider:
        # The captured choice belongs to this request, even if settings changed meanwhile.
        if not route.model:
            raise ProviderError(_missing_model(route.task))
        return OllamaProvider.for_host(self.host, route.model)

    def draft_backend(self, ctx: Any, route: ResolvedRoute) -> None:
        return None  # core's image backend wraps provider()

    def translation_backend(self, ctx: Any, route: ResolvedRoute) -> None:
        return None  # core's translation backend wraps provider()

    def slide_translator(self, ctx: Any, route: ResolvedRoute) -> None:
        return None  # core wraps provider()

    def task_runner(self, ctx: Any, route: ResolvedRoute) -> None:
        return None  # core's provider_tasks

    def chat_surface(self, *args: Any, **kwargs: Any) -> None:
        return None  # core's provider chat view

    def settings_section(self) -> Widget:
        from .settings import OllamaTab  # Qt only when Settings opens

        return OllamaTab(self.host, self).page
