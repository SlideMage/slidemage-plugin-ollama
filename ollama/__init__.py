# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ollama: local AI models for SlideMage's chat, drafting and translation, through the Ollama server on
this computer (or another one the user sets). Nothing leaves the computer when the server is local.

SlideMage imports this package and calls activate() once at start."""


def activate(host) -> None:
    """Register the plugin's contributions with SlideMage through host.

    Empty until step 5b: the AI route and the settings section are registered on SlideMage's AI host API
    (add_ai_route, add_settings_section)."""
