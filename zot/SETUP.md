---
title: Zot Registry Setup How-To (Kerbol-Next)
tags:
  - homelab
  - zot
  - quay
  - howto
date: 2026-10-31
status: staged-not-deployed
---

# Zot Registry Setup — How-To

Replaces the Quay stack with Zot: single container, file storage on the array, authentik OIDC login, built-in Trivy CVE scanning, and mirroring of the same 7 repos Quay currently mirrors.

**Staged, validated, not deployed.** `make validate`: 0 errors, 0 warnings (zot compose + resources.toml green).

## What is staged

| File | Purpose |
|---|---|
| `zot/compose.yaml` | Single `zot` service, image `ghcr.io/project-zot/zot:v2.1.22` (the only public registry — Docker Hub `zotregistry/zot` does not exist; the `2-ui`/`2-fips` tags are Docker Hardened Images at `dhi.io/zot` behind a login. The ghcr release image is the **full build**: UI/search/sync/Trivy built in), storage at `/mnt/user/zot` (array), `mem_limit: 4g`, healthcheck via the built-in `zot healthcheck` command |
| `zot/frontend.network.compose.yaml` | Puts zot on the external `frontend` network — the network newt (Pangolin tunnel agent) exposes to the VPS |
| `zot/labels.unraid.compose.yaml` | selfh.st icon (`zot-registry`) for the Unraid UI |
| `zot/config.json` | zot config: OIDC (authentik), UI, search + Trivy CVE (24h DB refresh), metrics, GC every 168h, and the **sync extension with 7 mirror rules matching Quay** |
| `zot/credentials.json` / `zot/sync-credentials.json` | **Not in git.** Generated in the run directory on every deploy by the stack's interpolated `pre_deploy` hook in `resources.toml` (see Phase 0.2). Kept out of `config_files` deliberately: `validate_files` only asserts that *listed* paths exist on disk (before the hook runs — that's what failed on the listed-but-absent `entrypoint.sh`); files that exist without being listed are never checked |
| `komodo-resources/resources.toml` | `[[stack]] name = "zot"` — server Unraid, linked repo `kerbol-next`, run dir `zot`, 3 compose files + 1 config file (`config.json`), env: `ZOT_VERSION = v2.1.22`, `ZOT_STORAGE = /mnt/user/zot`, `ZOT_MEM_LIMIT = 4g`, plus the `pre_deploy` hook that writes the two credential files |

Mirror rules (translated from Quay's `repomirrorconfig` + `repomirrorrule`, 7-day refresh each). **Polled** = zot actively copies on a schedule (requires the upstream to support `_catalog`). **Pull-through** = zot fetches on first pull, then re-validates cached manifests every 168h — this mode is mandatory for ghcr.io and docker.io, which don't serve `_catalog` and will otherwise log `failed to list repositories ... unauthorized` and never mirror:

| Upstream | Mode | Local destination | Tag filter (Quay rule → zot regex) |
|---|---|---|---|
| quay.io/projectquay/quay | pull-through | /projectquay/quay | `3.18*, 3.19*` → `^3\.(18|19)` |
| quay.io/projectquay/clair | pull-through | /projectquay/clair | `4.8*, 4.9*` → `^4\.(8|9)` |
| registry.redhat.io/offline-knowledge-portal/rhokp-rhel9 | pull-through | /redhat/offline-knowledge-portal/rhokp-rhel9 | `latest` → `^latest$` (syncTimeout 12h, was skopeo 43200s) |
| ghcr.io/goauthentik/server | pull-through | /goauthentik/server | `2025.*, 2026.*` → `^(2025|2026)\.` |
| ghcr.io/sillytavern/sillytavern | pull-through | /sillytavern/sillytavern | `latest` → `^latest$` |
| git.mcdade.app/homeserver/media-downloader | polled (168h) | same path | all tags |
| docker.io/ayufan/proxmox-backup-server | pull-through | /ayufan/proxmox-backup-server | `v4, v4.*` → `^v4(\.\|$)` |

For the pull-through entries, clients (Komodo, compose, scripts) must pull as `zot.mcdade.app/<destination>:<tag>` once for the image to enter local storage; afterwards it stays warm and only re-checks upstream weekly.

**Deletion semantics (verified in v2.1.22 source):** sync is add/update-only — if an image is deleted upstream, zot never removes the local copy (upstream not-found is treated as skip-and-continue for polled sync, and pull-through keeps serving the cached manifest despite the failed upstream check). Removal is yours to drive: retention policy (`storage.retention`), manual UI/API delete, then GC reclaims the blobs.

`http.compat: ["docker2s2"]` is set globally (upstreams serve Docker schema 2 images). `onlySigned` left at the default (off) — Quay's `unsigned_images: false` was evidently not blocking unsigned upstreams in practice (sillytavern mirrors fine), so parity = off. Flip `"onlySigned": true` in `config.json` if you want strict signed-only.

**v2 config gotchas (all hit live, all fixed in the staged file):** no top-level `"version"` key (v2's strict loader rejects it and zot crash-loops); every extension needs explicit `"enable": true` — `"ui": {}` means *disabled* and the web UI 404s while `/v2/` works fine; image lives only on `ghcr.io/project-zot/zot` (Docker Hub `zotregistry/zot` doesn't exist; the `2-ui` tags are Docker Hardened Images at `dhi.io/zot` behind a login).

---

## Phase 0 — MANUAL STEPS BEFORE COMMIT & PUSH

Nothing deploys correctly until these are done. They are the only manual steps.

### 0.1 Create the authentik OIDC application

In authentik admin (https://auth.mcdade.app):

1. **Providers → Create** → type **OpenID Provider**:
   - Provider name: `zot`
   - Client type: **confidential**
   - Client ID: (auto or set `zot`)
   - Client secret: generate
   - Signing/encryption algorithm: RS256 (default)
   - Scopes: make sure `openid`, `email`, and `groups` are included (add `groups` to the defaults)
   - **Redirect URIs**:
     - `https://zot.mcdade.app/zot/auth/callback/oidc`
2. **Applications → Create**: name `zot`, attach the `zot` provider, no access policy (or restrict to your group if preferred).
3. Copy the **Client ID** and **Client Secret**.

> The config already assumes issuer `https://auth.mcdade.app/application/o/zot/` — that's why the application slug must be `zot`. If you name it differently, update `issuer` in `zot/config.json` before pushing.

### 0.2 Create four Komodo Variables

Same scope as the existing `QUAY_POSTGRES_PASSWORD` (Unraid server):

| Variable | Value |
|---|---|
| `ZOT_OIDC_CLIENT_ID` | from 0.1 |
| `ZOT_OIDC_CLIENT_SECRET` | from 0.1 |
| `ZOT_REDHAT_USER` | your registry.redhat.io username |
| `ZOT_REDHAT_PASS` | your registry.redhat.io password/token |
| `ZOT_FORGEJO_USER` | Forgejo username for registry pulls (git.mcdade.app) |
| `ZOT_FORGEJO_TOKEN` | Forgejo access token, scope `read:package` |

**Fill the credential files via the stack's `pre_deploy` hook.** Komodo copies linked-repo **files** verbatim — a live deploy proved the templates arrive literal — but it *does* interpolate `[[VAR]]` into `pre_deploy.command` (source: `lib/interpolate` covers `pre_deploy`/`post_deploy`) and runs it via `bash -c` in the run directory after the repo clone, before `compose pull/up` (`run_shell_command` passes the string verbatim). The two credential files are therefore generated at deploy time and never committed; they are also kept out of `config_files` so the pre-deploy existence check ignores them.

One loader quirk governs everything you write in `resources.toml` (`bin/core/src/sync/mod.rs::escape_between_triple_string`): before TOML-parsing, Komodo splits the whole file on the triple-double-quote delimiter and **doubles every backslash between pairs**. Net effect: inside triple-quoted blocks you write each backslash **exactly once** and it survives literally (preprocess-doubling + TOML-unescape cancel out). Consequences: use triple-double-quoted strings (NOT triple-single-quote literal blocks — those skip the preprocessing, and worse, one stray triple-double-quote token anywhere — even in a comment — flips the pair parity and corrupts every block after it; this shipped literally corrupted `printf` format strings, producing `\n` text instead of newlines, live 2026-11-02).

```toml
[stack.config.pre_deploy]
shell_mode = true
command = """
cat > .zot_oidc_id.raw <<'ZOT_RAW_1'
[[ZOT_OIDC_CLIENT_ID]]
ZOT_RAW_1
cat > .zot_oidc_secret.raw <<'ZOT_RAW_2'
[[ZOT_OIDC_CLIENT_SECRET]]
ZOT_RAW_2
cat > .zot_rh_user.raw <<'ZOT_RAW_3'
[[ZOT_REDHAT_USER]]
ZOT_RAW_3
cat > .zot_rh_pass.raw <<'ZOT_RAW_4'
[[ZOT_REDHAT_PASS]]
ZOT_RAW_4
esc() { sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' "$1" | head -n 1; }
printf '{\n  "clientid": "%s",\n  "clientsecret": "%s"\n}\n' "$(esc .zot_oidc_id.raw)" "$(esc .zot_oidc_secret.raw)" > credentials.json
printf '{\n  "registry.redhat.io": {\n    "username": "%s",\n    "password": "%s"\n  }\n}\n' "$(esc .zot_rh_user.raw)" "$(esc .zot_rh_pass.raw)" > sync-credentials.json
rm -f .zot_oidc_id.raw .zot_oidc_secret.raw .zot_rh_user.raw .zot_rh_pass.raw
chmod 600 credentials.json sync-credentials.json
"""
```

Quirks baked into this block (all validated end-to-end through a simulation of Komodo's loader with hostile secret characters): **Unraid has no `python3`** — the hook uses only shell + GNU `sed`/`printf` (a live deploy died on `python3: command not found`); backslashes are written **exactly once** inside the triple-double-quoted command because Komodo's preprocess-then-TOML round-trip preserves them; heredoc bodies/terminators stay flush-left; secrets route through raw heredoc files through `sed` JSON-escaping (`\`→`\\`, `"`→`\"`) into `printf` args so `"` and `\` can't corrupt the JSON; raw temps are `rm`'d and outputs get mode 600. Komodo also masks secret-marked Variables in deploy logs (`<ZOT_OIDC_CLIENT_SECRET>`), so keep the secret checkbox on. The secrets themselves live only in Komodo Variables and in the two generated files on the server; git keeps nothing.

### 0.3 Get the Red Hat credentials (if not already on hand)

They live in Quay's DB (mirror id 5, repo 957). Extract on Kerbol-Next — **keep the output to yourself, do not paste it back**:

```bash
docker exec quay-postgresql sh -c 'psql -U "$POSTGRES_USER" -d quay -c "SELECT external_registry_username, external_registry_password FROM repomirrorconfig WHERE repository_id = 957;"'
```

(Alternatively re-register a token at console.redhat.com — cleaner than reusing the stored one.)

---

## Phase 1 — Commit and push (yours to run)

```bash
cd /home/jmcdade/Projects/stacks
git add zot komodo-resources/resources.toml
git commit -m "Add zot stack: OCI registry with OIDC, Trivy scanning, mirroring"
git push
```

Push is the deploy trigger — the workflow deploys the `zot` stack to Kerbol-Next automatically. The run dir `/mnt/user/stacks/repos/kerbol-next/zot/` and the storage dir `/mnt/user/zot` are created by Docker/Komodo on first deploy; no manual pre-creation needed.

> Note: your uncommitted `mem_limit: 16g` edit in `quay/compose.yaml` is still in the working tree — commit it with this or separately, your call.

## Pangolin — expose `zot.mcdade.app` (do this right after the deploy lands)

Pangolin on the VPS is the only reverse proxy in the loop: it terminates TLS and forwards through the newt agent to the `frontend` network on Unraid — the same path the `quay.mcdade.app` route already uses. The zot stack joins that network automatically (`frontend.network.compose.yaml`), so nothing on Unraid changes; you just add one resource.

1. Log in at **https://pangolin.mcdade.app** (email/password or the Authentik button).
2. Open **Resources** — the same screen where your `quay.mcdade.app` route lives — and create a **new resource**. Use the existing quay resource as the template; only these values differ:
   - **Type:** HTTP/HTTPS (public, protocol-aware reverse proxy — same as quay)
   - **FQDN:** `zot.mcdade.app`
   - **Site:** `Unraid` (the site attached via newt)
   - **Backend target:** `zot:8080` (container name on the frontend network, exactly like `quay:8080`)
   - **TLS:** leave default — Pangolin issues the cert for the new subdomain under your existing wildcard; no DNS change needed (the CF wildcard already points `*.mcdade.app` at the VPS)
   - **Access policy:** mirror whatever the quay resource has. Zot enforces authentik login at the app layer, so either open or restricted-to-you works.
3. Save. No newt restart is required — it discovers the `zot` container by name on the frontend network.

## Phase 2 — Post-deploy verification

1. **Credential materialization check:** the `pre_deploy` hook must have generated the two files —

   ```bash
   ls -l /mnt/user/stacks/repos/kerbol-next/zot/*.json
   grep -c '\[\[' /mnt/user/stacks/repos/kerbol-next/zot/credentials.json /mnt/user/stacks/repos/kerbol-next/zot/sync-credentials.json
   ```

   Expect both credential files present as regular files with mode `600` and `0` grep counts (Variables wired correctly). Check the deploy log shows a **Pre Deploy** step. If they are *missing* entirely, the hook failed — note compose will then have created a *directory* named `credentials.json` (bind-mount source auto-creation), which is the giveaway. If ResourceSync never re-applied the stack after `pre_deploy` was added to `resources.toml`, the hook doesn't exist in Komodo's stack definition yet — trigger a resource sync first.

2. **Container health** — Unraid UI (or `docker ps`): `zot` should reach `healthy` within ~2 min. First boot also downloads the Trivy DB (tens of MB), so give it a few minutes before expecting scan results.

3. **Confirm it's exposed** — using the Pangolin resource from the section above (the `quay.mcdade.app` route is never touched — zot lives on its own domain alongside Quay until Quay is decommissioned):

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://zot.mcdade.app/v2/
   ```

   Expect `401` (auth required — proof zot is serving; anonymous pulls are denied by design).

4. **RAM baseline** — `docker stats zot --no-stream`. Expect hundreds of MB. This is the number to compare against Quay's 30 GB.

## Phase 3 — OIDC login test

1. Open https://zot.mcdade.app — you should get the zot UI and an **authentik** login button. Log in. (No route flips anywhere: `externalUrl` and the authentik redirect are both `zot.mcdade.app`, and Quay keeps serving `quay.mcdade.app` untouched.)
2. In the zot UI, generate an **API key** (your browser session is authentik; the key is for `docker login`).
3. Test pull auth from any box:

   ```bash
   echo '***' | docker login zot.mcdade.app -u <your-authentik-username> --password-stdin
   docker pull zot.mcdade.app/...   # any repo already synced (see Phase 4)
   ```

4. **If anything fails:** fix forward on zot (its storage and config are independent) or just `docker compose down` in the zot run dir — Quay was never modified and never went down, so there is nothing to roll back.

## Phase 4 — Mirroring + build pushes

1. **Sync starts immediately at first boot** (polled registries run their first cycle on startup, then every `pollInterval` — verified: media-downloader layers were copying seconds after the container came up). Watch progress in the logs:

   ```bash
   docker logs --tail 50 zot | grep -i sync
   ```

   **Optional fast pre-population:** to fill zot from your own Quay instead of waiting on upstreams, temporarily add this as a sixth entry in `config.json` → `extensions.sync.registries`:

   ```json
   {
     "urls": ["https://quay.mcdade.app"],
     "pollInterval": "1h",
     "content": [ { "prefix": "/**" } ]
   }
   ```

   (Requires the Quay-side repos to be pullable — they are, with the existing auth.) Remove the entry and re-deploy after one full pass; the 1h interval exists only for the bootstrap.

2. **ghcr.io / docker.io use pull-through, not polling** — already set in `config.json` (`"onDemand": true`, `"manifestCheckInterval": "168h"`). These registries don't serve `_catalog`, so a `pollInterval` on them fails with `failed to list repositories for ghcr.io: unauthorized` in the logs and nothing ever mirrors. After the first pull of `zot.mcdade.app/goauthentik/server:…` (or sillytavern / ayufan-pbs), the image is local and re-validates upstream weekly.

2b. **quay.io and registry.redhat.io are ALSO pull-through-only, and polling on them fails SILENTLY (proven live 2026-11-02):** quay.io `/v2/_catalog` answers `200 {"repositories":[]}` (public Quay disables catalog), and registry.redhat.io's catalog lists internal names (`redhat-prod/ansible-tower-36----ansible-tower`) that do not map to pull paths (`ansible-tower-36/ansible-tower`) — a prefix filter matches nothing either way, producing zero images and **zero errors**. v2.1.22 removed the old `content[].repositories` explicit list, so polling cannot target specific repos on these upstreams at all. The fix in `config.json`: `"onDemand": true` + `"manifestCheckInterval"` for both registries. Consequence: mirrored repos materialize **on first authenticated pull** (`docker pull zot.mcdade.app/redhat/offline-knowledge-portal/rhokp-rhel9:latest`), not on a schedule — and for `redhat/**` that pull must be by a redhat-group member or admin, since anonymous access is denied there.

2c. **`preferred_username` requires the `profile` scope** (proven live: zot logged `configured username claim missing or empty, falling back to "email" claim` at login). Authentik only puts `preferred_username` in userinfo/id_token when `profile` is among the requested scopes; `claimMapping` alone doesn't conjure it. `scopes` now includes `profile`. Sessions created before a config change carry the old identity — log out/in after deploy, and regenerate API keys bound to the old email identity.

3. **Komodo build pushes:** builds currently push to `quay.mcdade.app/homeserver/...`. In the zot UI, create a second API key with **write** scope (e.g. for `homeserver/**`) and point Komodo's push credential at `zot.mcdade.app` (new registry entry; the Quay robot account on `quay.mcdade.app` stays valid until Quay is decommissioned).

4. **Re-point image lines** (after builds + pulls are confirmed working through zot):
   - `quay/compose.yaml`: `quay.mcdade.app/homeserver/clair` → `zot.mcdade.app/projectquay/clair`
   - `redhat-knowledge/compose.yaml`: `…/homeserver/redhat-knowledge` → `…/offline-knowledge-portal/rhokp-rhel9` (or adjust the zot destination instead — see below)
   - `playwright-mcp/compose.yaml`: same treatment
   - App repos outside this repo that pull from `quay.mcdade.app` (media-downloader et al.) on their next deploy.

   **Path parity note:** zot destinations above were derived from the *upstream* paths. If you want to keep the exact Quay local paths (e.g. `homeserver/clair`), edit the `destination` values in `zot/config.json` before pushing (run the namespace query from the earlier message to enumerate them). With separate domains there is no path collision — old refs keep working against Quay while you migrate them one app at a time.

## Phase 5 — Decommission Quay (only after a week of zot serving everything)

```bash
# On Kerbol-Next, in the quay run dir:
docker compose -f compose.yaml -f frontend-network.compose.yaml -f labels.unraid.compose.yaml down
```

Then remove (or comment out) the `[[stack]] name = "quay"` entry in `resources.toml` in a follow-up commit, and delete the `quay.mcdade.app` route in Pangolin. Keep `/mnt/user/quay` (image blobs) as a cold backup until you're fully comfortable; deleting it later reclaims the rest. Frees ~10 GB idle / ~30 GB peak RAM and 5 containers.

## Rollback

There is no cut-over moment to roll back — zot and Quay run on separate domains and Quay is never modified. If zot misbehaves at any point before Phase 5, every image ref still pointing at `quay.mcdade.app` is unaffected; `docker compose down` in the zot run dir removes zot entirely.

## RAM expectations (the point of this)

- zot: single Go process, `mem_limit: 4g` as a hard guardrail (Quay leaked to 30 GB *uncapped*; a capped zot worst case is a restart, not an OOM chain).
- Steady state should sit in the low hundreds of MB; Trivy background scans add transient bumps.
- Watch for the failure mode we're escaping: monotonic RSS growth over days. If `docker stats` shows steady climb >100 MB/day, that's a zot bug to file — the 2023 analog (#2137) was fixed fast upstream.

## Phase 6 — RBAC & the `redhat` namespace (2026-11-02)

`config.json` now has `http.accessControl` (zot v2.1.22 schema — verified against the Go module source):

- **Baseline `**`**: anonymous + any-logged-in user = `read` (pull/browse) only. Push requires an explicit grant. **Note the switch:** with `accessControl` present, repos matching no pattern are deny-for-all-except-admins, so the `**` catch-all is load-bearing.
- **`redhat` + `redhat/**`**: only the `redhat` group gets `read/create/update/delete`. Empty anonymous/default = anonymous pull is **denied** here (401), unlike the rest of the registry. Two patterns needed because doublestar `redhat/**` does not match the bare name `redhat`.
- **`adminPolicy`**: `FlexibleToast` = full rights everywhere (bootstrap/lockout guard).
- **Identity = `preferred_username`** via `claimMapping.username` (was the default `email`). All identity strings in accessControl and every `docker login -u` must be the Authentik **username**. Any API key generated before this change was bound to the old email identity — regenerate.
- **Group membership = the OIDC claim, full stop.** Zot extracts claim `groups` (config `claimMapping.groups` to rename) from userinfo/ID token at login and *overwrites* its metadb profile with exactly that set (`OAuth2Callback` + `SetUserGroups`, verified in v2.1.22 source). The static `accessControl.groups` config does NOT apply to OIDC sessions — it's only consulted for htpasswd/mTLS logins — so it is deliberately absent here. Lockout guard is `adminPolicy` (matches by username in every flow). ⚠️ Verify the Authentik scope mapping bound to the zot provider emits group **names** (a builtin-style mapping returning `group_ids` UUIDs will silently match nothing). Check after login: `docker logs zot | grep -i groups` (zot logs `no groups claim values found` when the claim is missing).
- **registry.redhat.io mirror destination** moved to `/redhat/offline-knowledge-portal/rhokp-rhel9` — this materializes the namespace (sync writes bypass authz). The old local copy at `offline-knowledge-portal/rhokp-rhel9` is NOT auto-removed (sync is add/update-only): delete it in the UI/API after the new one lands. Re-point `redhat-knowledge/compose.yaml` at the new path.

Verify after deploy:

```bash
# anonymous: public repos still read, redhat denied
curl -s https://zot.mcdade.app/v2/_catalog                      # 200, may list redhat (catalog filter is pattern-optimistic)
curl -s -o /dev/null -w '%{http_code}\n' https://zot.mcdade.app/v2/redhat/ # expect 401
curl -s -o /dev/null -w '%{http_code}\n' https://zot.mcdade.app/v2/projectquay/quay/manifests/latest # 404/200 = authz passed
```

Then as a redhat member: `docker login zot.mcdade.app -u FlexibleToast` (fresh API key) and `docker pull zot.mcdade.app/redhat/offline-knowledge-portal/rhokp-rhel9:latest`; as a non-member it must 401/403.

**HTTP timeouts (live bug, 2026-11-02):** zot's built-in HTTP server defaults are `defaultReadTimeout = defaultWriteTimeout = 60s` (pkg/cli/server/root.go). Multi-GB blobs served off the array through the Pangolin tunnel blow past 60s → `failed to copy data into http response: i/o timeout` in logs, docker stalls/retries with no progress. `http.readTimeout`/`http.writeTimeout` now set to `30m` (verified field names in `HTTPConfig`; duration strings parse via mapstructure).

## Phase 7 — authenticated pull-through namespaces (docker/, ghcr/, quay/)

Three general-purpose pull-through namespaces so most images get a local copy via zot:

- `zot.mcdade.app/docker/<repo>` → docker.io (officials: `docker/library/alpine` or `docker/alpine` — regclient normalizes)
- `zot.mcdade.app/ghcr/<org>/<repo>` → ghcr.io
- `zot.mcdade.app/quay/<org>/<repo>` → quay.io
- `zot.mcdade.app/forgejo/<owner>/<repo>` → git.mcdade.app (Forgejo container registry; private packages require the `ZOT_FORGEJO_USER`/`ZOT_FORGEJO_TOKEN` sync credentials, `read:package` token — the pre_deploy hook writes them into `sync-credentials.json` under key `git.mcdade.app`)

Implementation: each onDemand registry now carries a SINGLE catch-all content rule `{ "prefix": "/**", "destination": "/<ns>" }` (no stripPrefix; simplified in `e1dde5a` — the earlier narrow per-repo rules are gone). Semantics verified in `pkg/extensions/sync/content.go`: local matching pattern = `destination + prefix-metas` (e.g. `docker/**`), `getRepoSource` strips the destination → upstream path. ⚠️ The short destinations from the Phase-1 table (`/projectquay/quay`, `/goauthentik/server`, `/sillytavern/sillytavern`, `/ayufan/proxmox-backup-server`) NO LONGER resolve — zot refs are always namespaced: `ghcr.io/goauthentik/server` → `zot.mcdade.app/ghcr/goauthentik/server`, quay mirrors → `/quay/projectquay/...`, redhat.io → `zot.mcdade.app/redhat/<path>` (rhokp keeps the same path it had under the narrow rule; the catch-all just makes every other redhat.io repo pullable the same way). No compose in this repo referenced the short paths (authentik pulls `ghcr.io` directly), so nothing broke; re-point the Phase 4 image lines to the namespaced paths instead.

Freshness: `manifestCheckInterval = 120h` (5 days) on docker/ghcr/quay entries — under Komodo's weekly auto-update poll, every weekly check is guaranteed to revalidate against upstream rather than be answered from the ≤120h cache (checks are lazy: the poll itself triggers the sync; the poll that discovers a new digest pays the upstream fetch). registry.redhat.io stays 168h.

RBAC: `docker/**`, `ghcr/**`, `quay/**` = `anonymousPolicy: []` (anonymous 401) + `defaultPolicy: ["read"]` (any logged-in user pulls). No create/update → pushing into these namespaces is admin-only; on-demand materialization is internal and unaffected.

Operational consequences:

1. **Consumers must authenticate to pull** (API-key `docker login` on every host/Komodo node pulling these namespaces; compose pulls from an unauthenticated node will 401).
2. **docker.io rate limits**: zot pulls docker.io anonymously (sync-credentials.json has no docker.io entry) → shared-IP 100 pulls/6h cap applies to zot's egress. If throttled, add a `docker.io` username/password entry to the sync-credentials pre_deploy pipeline.
3. Namespaces appear in UI/catalog only after first pull (on-demand) and only to authenticated users (catalog is permission-filtered).
