# Ollama plugin: integration plan

The plugin targets the unreleased API 1 additions in SlideMage's `feature/ai-routes` worktree. The plugin's
code imports SlideMage only through `slidemage.plugin_api`. It is not yet a loadable replacement on that
branch because the built-in route owns the same ID. `requires_slidemage` remains `>=10.0`; that version range
alone does not guarantee these unreleased additions exist.

## Implemented

- Activation registers the `ollama` route and a lazy AI settings section, without network or widget creation.
  The route's `settings_section()` returns `None` because `add_settings_section` registers the builder.
- Route methods accept `RouteContext`; providers and model discovery use its plugin settings and legacy
  address reader. If the plugin has no saved address, the old effective address remains in use without
  copying it into plugin storage. A saved plugin address wins; explicitly clearing it falls back to `OLLAMA_HOST` or
  localhost. Core owns the persisted task choices and already maps the old `local` route ID to `ollama`.
- Model discovery runs in a background worker, caches by effective address, filters tool and vision models,
  discards superseded results, and calls `model_choices_changed` on the UI thread after publication. Address
  changes also notify the host. Core's regular model grid and `fill_model_combo` now query route choices.
- Settings administration uses `acquire_route_task` to share the Ollama reservation with core AI work, and
  releases it on cancellation, scheduling failure, worker completion or page destruction. Pull progress is
  sent through `run_in_background_with_progress` and logged on the UI thread. Workers capture the address and
  do not access widgets or host settings.
- The plugin supplies `provider()` and returns `None` for the optional task, image, translation and chat
  adapters, as the built-in Ollama route does. Core supplies those adapters and its provider chat view.
  `chat_surface_v2`, terminal widgets, hook context, task/image/translation helpers, `CancelToken`, and
  `legacy_secret` are for implementations with their own surfaces, processes, hooks, task runners or secrets;
  this provider has none, so no calls are needed. The abortable HTTP transport and route task IDs/labels
  remain local; the API does not export those provider details or the route ID/label constants.

## Remaining integration gaps and recommendations

1. **Duplicate route:** `ai/routes.py` registers built-in Ollama before accepting plugin routes and rejects
   the duplicate ID. Remove the built-in registration in the coordinated core extraction, preserving `ollama`
   and persisted choices. The plugin cannot safely override the registry.
2. **Built-in UI paths:** `ui/workspace/chat/view.py` and `chat/session.py` still list Ollama models through
   the built-in provider; `ui/ai_models.py` and `ui/dialogs/settings_ai.py` still populate the built-in cache;
   the assistant header reads the legacy host directly. Other built-in settings/discovery code also remains.
   These can show or query the wrong server after a plugin address is saved. Convert those paths to the route's
   cache/context and remove built-in-only polling and settings as part of extraction. The public host currently
   has no direct call to update the legacy-only translation-prompt model list or the header's host text.
3. **Legacy reader side effect:** the public context describes legacy readers as non-mutating, but the current
   host delegates to `app_settings.get_ollama_host()`, whose `load()` migrates old project-level machine keys
   into the machine store. The plugin does not write legacy settings itself. Make the host's reader honor its
   non-mutating contract, or explicitly document that existing core migration can run on read.
4. **CI and release source:** the Check workflow does not install SlideMage and the API additions are not in a
   released host. Pin an agreed SlideMage revision or installable release in CI and set the compatibility range
   once a release containing the additions exists. Do not claim `>=10.0` alone proves compatibility.
5. **Transport verification:** the restricted runner replaces loopback sockets with in-memory streams. Run the
   suite on a TCP-enabled runner to exercise actual socket shutdown and HTTP interaction.

A full loader/UI integration test requires the coordinated core extraction above; this repository cannot
complete it on the current worktree by itself.
