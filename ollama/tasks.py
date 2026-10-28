# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Task identifiers used by the public AI route contract."""
CHAT, IMAGE_DESCRIPTION, TRANSLATION, FIXES = "chat", "image_description", "translation", "fixes_notes_transcripts"
TASKS = (CHAT, IMAGE_DESCRIPTION, TRANSLATION, FIXES)
TASK_LABELS = {CHAT: "Chat", IMAGE_DESCRIPTION: "Image descriptions", TRANSLATION: "Translation",
               FIXES: "Fixes, notes, transcripts"}
