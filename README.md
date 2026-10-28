# Ollama

Runs SlideMage's AI features on a model you have installed through [Ollama](https://ollama.com): drafting slides,
translating, describing images, and the agent chat. The Ollama tab of the Settings sheet shows the server's
address and the models installed there, and updates or deletes them.

- **Local models.** The features use the Ollama server you set: by default `http://localhost:11434` on this
  computer, or the `OLLAMA_HOST` environment variable.
- **Model management.** Update pulls the latest weights of an installed model (a custom model you built with
  `ollama create` is relinked to its refreshed base). Delete removes the model's files from Ollama's store.
  Both are refused while an AI task is using the Ollama server.

**Privacy.** When the server is on this computer, nothing leaves the computer: the prompts, the slides and the
answers stay between SlideMage and Ollama. The plugin contacts only the server address you set, and only for the
requests of the features above and the model list. If you point it at another machine, that machine gets the same
requests.

**Requirements.** Ollama installed and running (ollama.com), and at least one model pulled, for example
`ollama pull qwen3`. The agent chat needs a model that can call tools.

## Development

Step 5b is a **draft** against the planned AI host API. `ollama/host.py` defines the local Protocol and neutral
type stand-ins until `slidemage.plugin_api` provides them. The plugin is not yet integrated with a released host.

The plugin follows the [SlideMage plugin template](https://github.com/stpalan/slidemage-plugin-template) and the
[plugin conventions](https://slidemage.org/plugins/submit/).

`python build.py --check` checks the plugin against the catalogue's conventions: `plugin.toml`, the package and
its `activate(host)`, the imports, no compiled files, and a `CHANGELOG.md` entry for the version. The **Check**
workflow runs it on every push.

`python build.py` checks and then builds `dist/ollama-<version>.smplugin` (a zip of `plugin.toml`, `LICENSE` and
the package) and its `.sha256`.

`python -m pytest` runs the tests; the Check workflow runs them too. The draft tests use a FakeHost and need
pytest and PySide6, without SlideMage on the source path. Qt runs offscreen; the HTTP tests normally use a
loopback server and fall back to in-memory HTTP streams when socket creation is forbidden.

`docs/PLAN.md` lists the completed import replacements and the remaining host integration work;
`docs/Handover.md` says where the plugin stands.

### Releasing a version

1. Raise `version` in `plugin.toml` and add a line to the end of `CHANGELOG.md`:
   `v1.1.0 (2026-11-02, reason): What changed.`
2. Commit, then tag and push: `git tag v1.1.0 && git push origin v1.1.0`.
3. The **Release** workflow checks that the tag matches the version, builds the asset and publishes the GitHub
   release with it.

A published version never changes: never replace or delete its asset; a fix is a new version.
