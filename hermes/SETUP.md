# hermes — derived image (upstream stable + Mnemosyne)

Komodo build `hermes` (builder Unraid) pushes `zot.mcdade.app/homeserver/hermes:<tags>`
with `auto_increment_version = true`. Zot retention (zot/config.json) prunes tags
older than 4 weeks and reaps untagged manifests; deploy from the bare major tag
(e.g. `:1`), never `:latest`.

## One-time bootstrap (per Hermes home, after first container start)

The venv is in the image; the plugin wrapper lands on the data volume once:

    docker exec -u hermes hermes bash -lc \
      'HERMES_HOME=/opt/data /opt/mnemosyne-venv/bin/mnemosyne-hermes install --mode wrapper --python /opt/mnemosyne-venv/bin/python'
    docker exec -u hermes hermes /opt/hermes/.venv/bin/hermes config set memory.provider mnemosyne
    docker exec -u hermes hermes /opt/hermes/.venv/bin/hermes gateway restart

Verify: `hermes memory status` shows `Provider: mnemosyne`.

The wrapper points at `/opt/mnemosyne-venv`, a path that is stable across image
rebuilds — it does NOT need to be re-run after an image update. Only if the
wrapper format itself changes: re-run the first command with `--force`.

## Recommended env (set on the container)

    MNEMOSYNE_HOST_LLM_ENABLED=true   # route consolidation LLM through Hermes' own provider (no local GGUF download)

Local embeddings (fastembed) download a small model on first use into the data
volume cache.

## Multiple profiles

Each profile needs `memory.provider: mnemosyne` in its own config; symlink
`<profile>/plugins/mnemosyne` to the base `/opt/data/plugins/mnemosyne` (or a
profile-local wrapper install). Set `memory.mnemosyne.profile_isolation: true`
for separate per-profile banks.

## Python bumps upstream

Cannot break this setup: the venv is built in the same image as the interpreter.
That is the whole point of the derived image.
