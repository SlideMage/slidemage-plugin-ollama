# Ollama plugin: handover

Where the plugin stands after step 5a of [PLAN.md](PLAN.md), and what to know before step 5b.

**Maintainer:** Stefan Palan. Built on Jürgen Fleiß's side while Stefan waits for SlideMage's plugin API
(`add_ai_route`, `add_settings_section`).

## Where it is

- **Local repository:** `~/github/slidemage-plugin-ollama`, branch `plugin-ollama`, no remote yet. Its history is
  SlideMage's `feature/anthropic-stdlib`, filtered with git-filter-repo to the Ollama paths. It moves to its own
  public repository in step 6.
- **Commits:** none yet in this session. The split is at the end of PLAN.md.
- **Working tree:** the moved files were stored with CRLF in the working tree but LF in the repository (the
  history's form). They are LF now, as `.gitattributes` (`* text=auto eol=lf`, from the CampusOnline plugin)
  wants, so the diffs against the history are the real changes only.

## What is in it

```
plugin.toml              id ollama, version 1.0.0, api_version 1, requires_slidemage >=10.0
ollama/                  the plugin's package
  __init__.py            activate(host), still empty (5b)
  provider.py            OllamaProvider, InstalledModel: the chat, streaming, capabilities, the agent's messages
  admin.py               pull, delete, relink of a custom model (stdlib only)
  settings.py            the Ollama tab of the Settings sheet (PySide6, on SlideMage's kit widgets)
tests/
  conftest.py            the `server` fixture
  fake_http.py           a local stand-in for Ollama's HTTP API
  test_provider.py       the 7 tests of OllamaProvider
build.py, .github/       from the template; Check also runs pytest
website/icon.svg         still the template's placeholder
```

Not in it, and to stay in SlideMage: the neutral provider code (`ChatProvider`, `ProviderError`, `_HttpProvider`,
the Anthropic provider, the route resolver), the agent types, and the Settings sheet.

## Running the checks

```
python3 build.py --check                                  # reports the SlideMage imports listed in PLAN.md
PYTHONPATH=/home/juergen/github/SlideMage/src:. /home/juergen/github/SlideMage/.venv/bin/python -m pytest -q
```

`build.py --check` fails until 5b: eight imports of SlideMage internals in `ollama/`, all listed in PLAN.md. It
is not to be weakened: step 5b removes each import.

The tests need SlideMage's source on the path: `ollama/provider.py` imports `slidemage.app_settings`,
`slidemage.project` and the neutral names from `slidemage.ai.providers`, and the test file imports the neutral
exceptions from there.

## Things to know before 5b

- **Two copies of the Ollama code until SlideMage drops its own.** SlideMage's `ai/providers.py`,
  `ai/ollama_admin.py` and `ui/dialogs/settings_ollama.py` are still in SlideMage. Removing them there is SlideMage's
  step, after the plugin is registered; until then, with the plugin installed, there are two Ollama providers.
- **The Check workflow's install line** (`git+https://github.com/stpalan/SlideMage.git@feature/plugin-host`) is
  the CampusOnline plugin's. Check that branch has `slidemage.ai.providers` and the Ollama code, or the tests fail
  on import, before relying on Check.
- **`fake_http.py` keeps `ollama_ndjson` and `wait_for_first_piece`.** No test in this plugin uses them yet. SlideMage's
  `tests/test_agent_ollama.py` does, for `stream_turn()`; those tests are not moved (not in step 5a), and whether they
  come to the plugin is open.
- **The settings keys** (`ollama_host`, `ai_models.local`) are SlideMage's. The migration in 5b is one read on
  activation; no one has decided yet who writes the plugin's own settings file.
