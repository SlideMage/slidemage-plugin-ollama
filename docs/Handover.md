# Ollama plugin: handover

Status, 2026-10-28: **step 5b DRAFT implemented and tested against a local host Protocol**. The AI additions
are still being built on SlideMage's `feature/ai-routes`; this is not a claim of compatibility with today's
`slidemage.plugin_api`. The existing host on `feature/plugin-host` does not yet have the required AI surface.
See [PLAN.md](PLAN.md) for the import replacement table and remaining integration decisions.

Maintainer: Stefan Palan. Repository: `~/github/slidemage-plugin-ollama`, branch `plugin-ollama`. The 5a
extraction/template commits are already in its history. No public repository or release has been created.
SlideMage and its worktrees were only read for this draft; their sources were not modified.

## Package and tests

| File | Responsibility |
|---|---|
| `ollama/host.py` | `Host`, `Settings`, `AiRoute`, `ResolvedRoute` Protocols and neutral AI/HTTP stand-ins; all labelled DRAFT |
| `ollama/__init__.py` | one-time legacy migration, then route and lazy AI settings section registration |
| `ollama/route.py` | all planned AiRoute members, Local problem messages, cached async model discovery and provider factory |
| `ollama/provider.py` | Ollama chat, streamed agent turns, capabilities and installed models; `for_host` captures the address |
| `ollama/admin.py` | unchanged stdlib update, delete and relink implementation |
| `ollama/settings.py` | controller composing a host Page; address saving, discovery, update/delete and page-lifetime handling |
| `tests/fake_host.py` | recorded registrations/settings/messages, queued real worker threads and lazy Qt widget factories |
| `tests/test_activate.py` | activation without Qt/SlideMage/network, migration, route behaviour and model-cache races |
| `tests/test_provider.py` | original seven provider tests plus two streaming tests, using the draft neutral types |
| `tests/test_settings.py` | offscreen Qt tests: host saving, confirmations, task-slot refusal/release, worker failures and deleted pages |
| `tests/fake_http.py`, `tests/conftest.py` | local HTTP server; in-memory HTTP transport when TCP creation is forbidden |

Activation performs only local settings reads/saving and registrations. The Settings module is imported only
when its registered builder runs. Discovery first starts on a picker query or when Settings opens, on a host
worker. Chat needs tool-capable models, image descriptions need vision, and translation/fixes accept all models.
There is no default-model fallback or network probe during readiness checks or provider construction.

## Host calls the draft needs

| Call | Purpose/status |
|---|---|
| `add_ai_route(route)` | planned 3d; registers `OllamaRoute` |
| `add_settings_section(page, title, build)` | planned 3c; page `ai`, title `Ollama`, lazy no-argument builder |
| `settings()` → `get(key, default)`, `set(key, value)`, `save()` | existing host API; plugin-owned persisted JSON values |
| `legacy_setting(key)` | **proposal**: cheap read-only access to old `ollama_host` and `ai_models`, machine over project |
| `run_in_background(work, done)` | existing host API; work off-thread, completion on UI thread even after the requesting page closes |
| `log(message)` | existing call; **proposal** that it also accepts worker-thread admin progress |
| `show_message(text, error=False)` | existing host API; UI-thread error reporting |
| `try_start_ollama_task(what)`, `end_ollama_task()` | **proposals** replacing app calls; share the slot with core AI batches, acquire/release on UI thread |
| `Page()`, `Field(label, widget)`, `caption(text, tone="")` | existing widget surface; Page supplies `body`, `changed` and QWidget's `destroyed` signal |
| `Button(text, compact=False)`, `row(*widgets)` | **proposals** for the remaining settings widgets |

`Host`, `Settings` and the neutral stand-ins are stdlib-only. The neutral agent messages, exceptions and reply
types must be replaced together with public API imports: mixing the stand-ins with core types would break
exception handling and `isinstance` checks. The copied abortable `HttpProvider` and `error_detail` are also
stand-ins; their public export names are not promised by phase 3d.

## Settings and outstanding seams

On first activation, `legacy_setting` reads the two old keys once. The plugin copies the address to
`ollama_host` and recognized task choices from `ai_models.local` to `models` (`{task: model}`), preserving
existing plugin choices, including empty strings. It saves `legacy_settings_migrated=True` even when there
is nothing to copy, so later starts never reread legacy settings. Saving failures are reported and retried.
Legacy settings are never written. The effective address is saved override, `$OLLAMA_HOST`, then localhost.

The migrated `models` values currently supply `default_choice(task)` and `problem(ctx, task)`. An explicit
resolved model is validated and used by `provider(ctx, route)`, even if the saved default differs or is empty.
Core still owns its model pickers and per-task selection; their store/context needs to be reconciled with
these migrated defaults when the concrete API lands. No context methods were invented in this draft.

`draft_backend`, `translation_backend`, `slide_translator`, `task_runner` and `chat_surface` return `None` for
core's provider adapters/tasks/view. Confirm this convention for every member with the actual registry.
`settings_section` is the same lazy builder registered by activation, not a second section. Core's future
notification for completed model discovery is not specified yet; until then a later `model_choices` query
sees the refreshed list. A Settings refresh updates both the page and that cache.

Workers capture the target address before starting. Closing Settings cannot strand the task slot: completion
releases it before considering widget updates, and late results never touch destroyed widgets. The settings
address is disabled during administration; a new provider sees later settings without changing an in-flight one.

## Verification

```sh
python3 build.py --check
/home/juergen/github/SlideMage/.venv/bin/python -m pytest -q
```

The draft check passes with the original import checker; no SlideMage internals or source path are needed.
The local suite has 40 passing tests. This runner forbids TCP sockets, so its provider tests used the fallback
in-memory HTTP streams (the stdlib HTTP parser, streaming and abort signalling still run). OS socket shutdown
needs a normal TCP-enabled test runner. Qt tests use `QT_QPA_PLATFORM=offscreen` automatically.

The Check workflow installs pytest and PySide6, without fetching the private SlideMage repository. Once the
real AI API is available, replace the draft imports and add tests against the supported SlideMage releases.
`plugin.toml` still has unreleased version 1.0.0, API version 1 and `requires_slidemage >=10.0`; those are release
intentions, not proof that the AI API exists. Publication and core removal remain separate work.
