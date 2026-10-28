# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ollama: local AI models for SlideMage's chat, drafting and translation, through the Ollama server on
this computer (or another one the user sets). Nothing leaves the computer when the server is local.

SlideMage imports this package and calls activate() once at start."""
from __future__ import annotations

from .host import TASKS, Host
from .route import OllamaRoute

MIGRATED_KEY = "legacy_settings_migrated"


def _migrate_settings(host: Host) -> None:
    """Copy only missing plugin values. The old SlideMage settings stay read-only."""
    settings = host.settings()
    if settings.get(MIGRATED_KEY):
        return
    old_host = host.legacy_setting("ollama_host")
    old_models = host.legacy_setting("ai_models")
    if settings.get("ollama_host") is None and isinstance(old_host, str):
        settings.set("ollama_host", old_host.strip())
    local = old_models.get("local") if isinstance(old_models, dict) else None
    current = settings.get("models")
    models = dict(current) if isinstance(current, dict) else {}
    if isinstance(local, dict):
        for task in TASKS:
            if task not in models and isinstance(local.get(task), str):
                models[task] = local[task]
    if models != current and models:
        settings.set("models", models)
    settings.set(MIGRATED_KEY, True)
    try:
        settings.save()
    except OSError as exc:
        # Do not claim success in memory either: a later activation must retry.
        settings.set(MIGRATED_KEY, False)
        host.log(f"Could not save migrated Ollama settings: {exc}")
        host.show_message(f"Could not save Ollama settings: {exc}", error=True)


def activate(host: Host) -> None:
    """DRAFT: migrate local settings and register, without Qt, network or workers."""
    _migrate_settings(host)
    route = OllamaRoute(host)
    host.add_ai_route(route)
    host.add_settings_section(page="ai", title="Ollama", build=route.settings_section)
