# Ollama plugin: plan

Turning SlideMage's Ollama features into the plugin `ollama`, released through the SlideMage plugin catalogue.
Built from SlideMage's `feature/anthropic-stdlib` history with git-filter-repo; the template is
[slidemage-plugin-template](https://github.com/stpalan/slidemage-plugin-template) (as in
`slidemage-plugin-campusonline`). Where things stand now: [Handover.md](Handover.md).

The plugin is everything that talks to an Ollama server:

- the **Ollama provider** (`OllamaProvider`, `InstalledModel`): the chat, drafting, translation, image description
  and the agent chat's streamed turns, against `/api/chat`, `/api/tags` and `/api/show`;
- the **model store** (`admin.py`): pull (update), delete and relink of custom models, against `/api/pull`,
  `/api/delete`, `/api/show` and `/api/create`;
- the **Settings tab** (`settings.py`): the server's address, and the installed models with Update and Delete.

What stays in SlideMage: the neutral provider code (`ChatProvider`, `ChatReply`, `ProviderError`, `Cancelled`,
`parse_json_object`, `_HttpProvider`, the Anthropic provider and the route resolver), the agent types, and the
Settings sheet itself. The plugin takes what it needs through the plugin API.

Each step is one commit (or a few); work pauses after each one for review.

## Steps

| | Step | Status |
|---|---|---|
| 0 | History: the Ollama paths of SlideMage's `feature/anthropic-stdlib` filtered out with git-filter-repo | done |
| 5a | Layout: `ollama/` package, the Ollama code split from `providers.py`, tests reduced, template adopted | done, not yet committed (split below) |
| 5b | Onto SlideMage's AI host API: `add_ai_route`, `add_settings_section`; the settings moved from `ollama_host` / `ai_models.local`; every import in the table below replaced | waits for the API |
| 6 | The public repository and 1.0.0 | waits for 5b |

### 5a. Layout

Moved with `git mv`, so the history follows:

| From (SlideMage) | To (plugin) |
|---|---|
| `src/slidemage/ai/providers.py` | `ollama/provider.py` (cut down to Ollama, see below) |
| `src/slidemage/ai/ollama_admin.py` | `ollama/admin.py` |
| `src/slidemage/ui/dialogs/settings_ollama.py` | `ollama/settings.py` |
| `tests/test_ai_providers.py` | `tests/test_provider.py` (cut down to the Ollama tests) |
| `tests/fake_http.py` | `tests/fake_http.py` (the Anthropic helpers dropped) |

Added: `ollama/__init__.py` (module docstring and an empty `activate(host)`), `tests/conftest.py` (the `server`
fixture; SlideMage's conftest is not loaded from this repository), and the template files.

`ollama/provider.py` keeps `OllamaProvider`, `InstalledModel`, `AGENT_NUM_CTX`, `_TOOL_FAMILIES_RE`, `_arguments`
and `_ollama_messages`. Not copied: `ChatProvider`, `ChatReply`, `ProviderError`, `Cancelled`, `Message`,
`parse_json_object`, `_HttpProvider`, `_shutdown_socket`, `_error_detail`, `_image_media_type`, and everything
Anthropic. `ollama/provider.py` imports those that Ollama still needs from `slidemage.ai.providers`, listed below.

### 5b. Onto SlideMage's AI host API

- `activate(host)` registers the Ollama route with `add_ai_route` (the chat, drafting and translation of the
  project's Local AI mode use `OllamaProvider` on the host the settings give), and the Ollama tab with
  `add_settings_section`, which `settings.py`'s `OllamaTab` becomes.
- The address moves from the `ollama_host` setting, and the Local AI model choice from `ai_models.local`, to the
  plugin's own settings. SlideMage's copies of the old keys are read once on activation and not written again.
- The Ollama code is removed from SlideMage in SlideMage's own step, after this one; until then SlideMage's
  copy and the plugin's copy both exist.
- `ollama/settings.py`'s `self.app` calls (`app.project`, `app.log`, `app.try_start_ollama_task`,
  `app.end_ollama_task`) become host calls: they are not imports, but the tab needs them.
- The tests import `slidemage.plugin_api` only, and run against the SlideMage that has the host (see the Check
  workflow's install line, which points at the branch that has it).

### Imports left (`python build.py --check` reports these)

Each one is SlideMage code the plugin imports today, and what replaces it. The `plugin_api` names are proposals
for Stefan's API, not existing names.

| File | Import | Replaced by | Note |
|---|---|---|---|
| `ollama/provider.py` | `slidemage.app_settings` (`DEFAULT_OLLAMA_HOST`, `get_ollama_host`) | plugin setting `ollama_host` read through the host; the default moves into the plugin | `for_project` changes with it |
| `ollama/provider.py` | `slidemage.ai.agent_types` (`AgentMessage`, `AssistantMessage`, `ToolCall`, `ToolResultsMessage`, `ToolSpec`) | `slidemage.plugin_api` agent types | neutral; stream_turn's contract |
| `ollama/provider.py` | `slidemage.ai.providers` (`ProviderError`, `Cancelled`, `ChatReply`, `Message`, `_HttpProvider`, `_error_detail`) | `slidemage.plugin_api`: `ProviderError`, `Cancelled`, `ChatReply`, `Message`, and the abortable base class `HttpProvider` (public, with `error_detail`) | `_HttpProvider` and `_error_detail` are private in SlideMage today |
| `ollama/provider.py` | `slidemage.project` (`ProjectContext`) | `slidemage.plugin_api` project type (`Project`) | used by `for_project(ctx)` only |
| `ollama/settings.py` | `slidemage.app_settings` (`get_ollama_host`, `set_ollama_host`) | the plugin setting, as above | |
| `ollama/settings.py` | `slidemage.deck_snapshot` (`format_size`) | `slidemage.plugin_api.format_size`, or a three-line copy in the plugin | |
| `ollama/settings.py` | `slidemage.ui.background` (`start_detached`) | `host.start_detached(thread)` | runs the admin worker off the UI thread |
| `ollama/settings.py` | `slidemage.ui.kit.widgets` (`Button`, `Field`, `Page`, `caption`, `row`) | the host's widgets (`Page`, `Field`, `caption`, `Button`, `row`) | as the CampusOnline plan's `Page`, `Field`, `caption` |
| `tests/test_provider.py` | `slidemage.ai.providers` (`Cancelled`, `ProviderError`) | `slidemage.plugin_api` | |

`ollama/admin.py` and `ollama/__init__.py` import nothing from SlideMage.

## Commit split (intended, nothing committed yet)

1. Renames only, with the files as they were in SlideMage: `git mv` of the five files, so `git log --follow` works.
2. The Ollama split: `provider.py` cut down, absolute imports, `settings.py` and `test_provider.py` imports,
   `fake_http.py` without the Anthropic helpers, `conftest.py`, `__init__.py`, the layout's `src/` removed.
3. The template: `plugin.toml`, `build.py`, `.github/workflows/`, `.gitattributes`, `.gitignore`, `LICENSE`,
   `pyproject.toml`, `README.md`, `CHANGELOG.md`, `website/icon.svg`.
4. `docs/PLAN.md` and `docs/Handover.md`.
