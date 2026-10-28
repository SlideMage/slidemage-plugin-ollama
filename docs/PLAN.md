# Ollama plugin: integration plan

5a extracted the provider and admin code. 5b now targets `slidemage.plugin_api` on SlideMage's
`feature/ai-routes` branch; the plugin is not yet a replacement for the built-in route in that branch.
`requires_slidemage` remains `>=10.0`. See [Handover.md](Handover.md) for verification and the handoff.

## Implemented

- Activation registers an `ollama` route and one lazy AI settings section using the real Host. It does no
  network I/O or widget construction. `settings_section()` returns `None` because the section is registered
  separately; otherwise `AiPage` builds it twice.
- The route follows the built-in route's resolved `Route` behavior: model readiness, no default, choice
  splitting and validation, labels, provider factory, and `None` for core's adapters, task runner and chat.
  Its asynchronous model cache filters chat for tools and image descriptions for vision. It starts discovery
  on the first query or Settings refresh and ignores superseded results. This scheduling differs from the
  built-in route's cache, which core explicitly fills.
- Neutral agent messages, tool types, replies and exceptions come from `slidemage.plugin_api`; the copied
  abortable HTTP transport remains local because the public API does not expose it. Task identifiers and
  labels are local constants. Buttons and layout use Qt because Host exposes only Page, Field and caption.
- Host `settings`, background workers, logging and messages replace the draft calls. Workers never use host
  calls or widgets; administration logs completion on the UI thread. The fake structurally provides the
  public Host methods, and unsupported services raise rather than pretending to work.

## API gaps and decisions before integration

1. **Duplicate route:** core registers its built-in `ollama` route before the loader accepts plugin routes;
   loader rejects the duplicate. Recommendation: remove the built-in registration as part of the coordinated
   core extraction, keeping the ID and persisted choices. The plugin alone cannot resolve this.
2. **Model discovery and notification:** core's `AiPage.set_installed` writes the built-in cache and
   `ModelsGrid._offered` special-cases the `ollama` ID instead of consulting this route. The public API has no
   call to notify the host that model choices changed or supply installed models to the existing pickers.
   Recommendation: make pickers route-driven and add a route-change notification, then use the plugin cache;
   remove the built-in-specific listing. The plugin's cache is otherwise visible on the next route query only.
3. **Address migration:** Host has no read-only legacy settings accessor for the old `ollama_host`. The plugin
   reads its own `ollama_host`, then `OLLAMA_HOST`, then localhost; an old saved core address is not migrated.
   Recommendation: expose a narrowly scoped legacy address read and copy it once, without overwriting an
   existing plugin setting. Core's `ai_profile` already maps old `local` model choices to `ollama`; those
   choices remain core-owned and are not copied into plugin settings.
4. **Shared Ollama task slot:** Host has no acquire/release service shared with core AI work. The plugin
   serializes its own model administration, but it cannot refuse administration while a core AI batch is
   running. Recommendation: expose a shared operation guard and use it for core work and plugin administration.
5. **Progress:** Host callbacks are UI-thread-only and no worker-to-UI progress dispatch is specified.
   Administration completion is logged on the UI thread, but live pull progress is not logged. Recommendation:
   expose a thread-safe progress dispatch or explicitly document a worker-safe logging call.
6. **Shared HTTP helpers:** the public API does not expose the abortable HTTP provider, error-body parser,
   `Message` alias, task IDs/labels or agent context-size constant. The plugin keeps equivalents locally.
   Recommendation: keep these provider details local unless core wants a stable shared transport/constants API.

7. **CI source:** the existing Check workflow still installs only pytest and PySide6 and describes local draft
   types; the suite now requires SlideMage's real API. Recommendation: select a pinned SlideMage checkout or
   installable release for CI before updating the workflow; the branch is not yet a released host.

Do not remove core's implementation or change the cross-repository API without a decision. Once agreed, test
loading, settings, discovery, migration and shared task coordination against the extracted core and then release.
