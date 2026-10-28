# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ollama AI route for SlideMage."""
from __future__ import annotations

from slidemage.plugin_api import Host
from .route import OllamaRoute


def activate(host: Host) -> None:
    """Register without network, background work, or Qt widgets."""
    route = OllamaRoute(host)
    host.add_ai_route(route)
    host.add_settings_section("ai", "Ollama", route.build_settings_section)
