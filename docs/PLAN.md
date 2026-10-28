# Ollama plugin: plan

Turning SlideMage's Ollama features into the plugin `ollama`, released through the SlideMage plugin catalogue.
Built from SlideMage's `feature/anthropic-stdlib` history with git-filter-repo; the template is
[slidemage-plugin-template](https://github.com/stpalan/slidemage-plugin-template).
Current state: [Handover.md](Handover.md).

| Step | Status |
|---|---|
| 0: filter the Ollama paths and their history | done |
| 5a: package layout, provider split, template and reduced tests | committed (`a6039e1`, `4270afb`, `1ea009b`, `1cb8fe3`) |
| 5b: route registration, settings section and migration | **DRAFT implemented against `ollama/host.py`; not integrated with a released AI host API** |
| 5b integration: replace the draft with `slidemage.plugin_api` and test with the real host | waits for SlideMage's phases 3a, 3c and 3d |
| 6: public repository and 1.0.0 release | waits for integration; nothing published |

## Scope and extraction

The plugin owns the Ollama provider (chat, streaming agent turns, image descriptions, translation and fixes),
the server address, and model administration (pull, delete and relink of custom models). SlideMage keeps the
AI features, prompts, resolver, provider wrappers and Settings sheet.

The history-preserving moves in 5a were:

| SlideMage | Plugin |
|---|---|
| `src/slidemage/ai/providers.py` (Ollama part) | `ollama/provider.py` |
| `src/slidemage/ai/ollama_admin.py` | `ollama/admin.py` |
| `src/slidemage/ui/dialogs/settings_ollama.py` | `ollama/settings.py` |
| `tests/test_ai_providers.py` (Ollama tests) | `tests/test_provider.py` |
| `tests/fake_http.py` (Ollama helpers) | `tests/fake_http.py` |

## 5b draft

The source of the names is SlideMage's `feature/ai-routes:docs/PLUGINS_REMAINING_PLAN.md`, sections 3a, 3c,
3d and phase 5b. Existing settings, background, message and basic widget signatures follow
`feature/plugin-host:src/slidemage/plugin_api.py`. Like CampusOnline at `9d5a6b9`, the plugin first documents
its needs as a local `typing.Protocol`, with a FakeHost for tests.

- `activate(host)` reads the two legacy settings locally, saves the migration marker, and calls
  `add_ai_route(route)` and `add_settings_section(page="ai", title="Ollama", build=route.settings_section)`.
  It imports no Qt, contacts no server and starts no worker.
- `OllamaRoute` has all of 3a's members: `id="ollama"`, `service="Ollama"`, `is_local=True`, all four tasks,
  `problem`, `model_choices`, `default_choice`, `choice_label`, `provider`, `draft_backend`,
  `translation_backend`, `slide_translator`, `task_runner`, `chat_surface`, `settings_section`.
  Missing models use the existing Local resolver texts; no route or model is silently substituted.
  Backend/runner/surface methods return `None` to request the core's provider wrappers and chat view.
- Model choices are cached and filter tools for chat, vision for image descriptions, and neither for
  translation/fixes. The first query schedules discovery through `run_in_background` and returns immediately;
  Settings can refresh the cache too. Old-address and superseded results are ignored.
- The settings section composes a host `Page`. Model discovery, update and delete use the host worker service.
  Workers capture the server address and never read settings or access widgets. Admin completion releases the
  app-wide Ollama slot on the UI thread, including after the page is destroyed or scheduling fails.
- `needs` is "Ollama installed"; `network` is "The Ollama server you set (by default on this computer)".

### Settings migration

The plugin's machine-local `settings()` store holds:

| Key | Value |
|---|---|
| `ollama_host` | address override; empty means `$OLLAMA_HOST`, then `http://localhost:11434` |
| `models` | `{task: model_name}` for the four tasks; migrated choices supply `default_choice(task)` |
| `legacy_settings_migrated` | `true` after saving the one-time migration |

Proposed `legacy_setting(key)` reads SlideMage's old `ollama_host` and `ai_models` once before the marker is set.
Only `ai_models["local"]` is copied; cloud and unknown task entries are ignored. Existing plugin values win,
including explicitly empty values. The legacy store is never changed. Failed saving is reported, leaves the
marker false, and is retried at the next activation. Providers capture the effective address when constructed.

### Import replacements (completed in the draft)

There are **no imports of SlideMage internals** in the package or its tests. The build import checker is unchanged.

| Former dependency | Draft replacement | Integration still required |
|---|---|---|
| `slidemage.app_settings` | plugin `settings().get/set/save`; `provider.ollama_host` and local default | confirm storage/migration semantics with core |
| `slidemage.ai.agent_types` | stand-ins in `ollama.host` | import the public agent types together; identity matters for `isinstance` |
| `slidemage.ai.providers` neutral types | `ChatProvider`, `ChatReply`, `Message`, `ProviderError`, `Cancelled` in `ollama.host` | import public API types together; exception identity must match the host |
| private `_HttpProvider`, `_error_detail` | local `HttpProvider`, `error_detail` stand-ins retaining current HTTP/abort behaviour | public export names are proposals beyond 3d's neutral-type list |
| `slidemage.project.ProjectContext`, `for_project` | `OllamaProvider.for_host(host, model)` | only the host's plugin store supplies the address |
| `slidemage.deck_snapshot.format_size` | small local `_format_size` in `settings.py` | none |
| `slidemage.ui.background.start_detached`, QThread worker | `host.run_in_background(work, done)` | ensure completion outlives the page |
| `slidemage.ui.kit.widgets` | host `Page`, `Field`, `caption`, `Button`, `row` | `Button` and `row` extend the existing API |
| `self.app.project/log/try_start_ollama_task/end_ollama_task` | `settings`, `log`, `try_start_ollama_task`, `end_ollama_task` on the host | shared task slot and worker-safe logging remain explicit proposals |

## Remaining integration work

1. Agree the draft signatures in `ollama/host.py` with the actual AI host API; replace the local stand-ins with
   public imports as one change. This draft intentionally has no fallback import of SlideMage internals.
2. Reconcile the core's per-route/per-task model store with the migrated plugin `models` defaults. The plan
   retains `ai_profile`'s choices; this draft's `problem(ctx, task)` checks the plugin default, while
   `provider(ctx, route)` validates the captured request's model. The context contract is still unspecified.
3. Agree how core model pickers learn that async discovery completed. The plan defines no change notification;
   the draft serves the result on the next `model_choices` call and supplies a callback to its Settings section.
4. Confirm that `None` from the backend methods requests core wrappers, and that section registration and
   `settings_section` metadata describe one section, without building it twice.
5. Run real-host contract/integration tests, remove core's Ollama implementation in SlideMage's own phase 7,
   and confirm the release requirements. Until then this is a draft, not a compatible released plugin.

## Checks

```sh
python3 build.py --check
/home/juergen/github/SlideMage/.venv/bin/python -m pytest -q
```

Tests use the local contract and FakeHost, without SlideMage on `PYTHONPATH`. Qt tests use the offscreen platform.
The HTTP fixture normally uses a loopback server; where TCP socket creation raises `PermissionError`, it uses
in-memory byte streams with the same stdlib HTTP client/handler. That fallback verifies wire data, streaming
and abort signalling, but not OS socket shutdown. CI installs only pytest and PySide6 during this draft.
