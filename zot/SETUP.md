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
| `zot/compose.yaml` | Single `zot` service, image `ghcr.io/project-zot/zot:v2.1.22` (the only public registry — Docker Hub `zotregistry/zot` does not exist; the `2-ui`/`2-fips` tags are Red Hat hardened-catalog images behind a subscription login. The ghcr release image is the **full build**: UI/search/sync/Trivy built in), storage at `/mnt/user/zot` (array), `mem_limit: 4g`, healthcheck via the built-in `zot healthcheck` command |
| `zot/frontend.network.compose.yaml` | Puts zot on the external `frontend` network — the network newt (Pangolin tunnel agent) exposes to the VPS |
| `zot/labels.unraid.compose.yaml` | selfh.st icon (`zot-registry`) for the Unraid UI |
| `zot/config.json` | zot config: OIDC (authentik), UI, search + Trivy CVE (24h DB refresh), metrics, GC every 168h, and the **sync extension with 7 mirror rules matching Quay** |
| `zot/credentials.json` | OIDC client id/secret — `[[ZOT_OIDC_CLIENT_ID]]` / `[[ZOT_OIDC_CLIENT_SECRET]]` placeholders |
| `zot/sync-credentials.json` | registry.redhat.io credentials — `[[ZOT_REDHAT_USER]]` / `[[ZOT_REDHAT_PASS]]` placeholders |
| `komodo-resources/resources.toml` | `[[stack]] name = "zot"` — server Unraid, linked repo `kerbol-next`, run dir `zot`, 3 compose files + 3 config files, env: `ZOT_VERSION = v2.1.22`, `ZOT_STORAGE = /mnt/user/zot`, `ZOT_MEM_LIMIT = 4g` |

Mirror rules (translated 1:1 from Quay's `repomirrorconfig` + `repomirrorrule`, 7-day poll each):

| Upstream | Local destination | Tag filter (Quay rule → zot regex) |
|---|---|---|
| quay.io/projectquay/quay | /projectquay/quay | `3.18*, 3.19*` → `^3\.(18|19)` |
| quay.io/projectquay/clair | /projectquay/clair | `4.8*, 4.9*` → `^4\.(8|9)` |
| registry.redhat.io/offline-knowledge-portal/rhokp-rhel9 | same path | `latest` → `^latest$` (syncTimeout 12h, was skopeo 43200s) |
| ghcr.io/goauthentik/server | /goauthentik/server | `2025.*, 2026.*` → `^(2025|2026)\.` |
| ghcr.io/sillytavern/sillytavern | /sillytavern/sillytavern | `latest` → `^latest$` |
| git.mcdade.app/homeserver/media-downloader | same path | all tags |
| docker.io/ayufan/proxmox-backup-server | /ayufan/proxmox-backup-server | `v4, v4.*` → `^v4(\.\|$)` |

`http.compat: ["docker2s2"]` is set globally (upstreams serve Docker schema 2 images). `onlySigned` left at the default (off) — Quay's `unsigned_images: false` was evidently not blocking unsigned upstreams in practice (sillytavern mirrors fine), so parity = off. Flip `"onlySigned": true` in `config.json` if you want strict signed-only.

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

These are referenced as `[[ZOT_OIDC_CLIENT_ID]]` etc. inside `credentials.json` and `sync-credentials.json` — Komodo interpolates them when writing the files to the run dir (verified post-deploy in Phase 2, because no other stack in this repo uses this pattern yet).

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

1. **Interpolation check (important — new mechanism for this repo):**

   ```bash
   grep -l '\[\[' /mnt/user/stacks/repos/kerbol-next/zot/*.json
   ```

   Must print **nothing**. If it lists files, Komodo did not interpolate the config files: put the real values into those two run-dir files by hand (`sed -i`), run `docker compose -f compose.yaml -f frontend.network.compose.yaml -f labels.unraid.compose.yaml up -d zot` in the run dir, and tell me — I'll fix the durable mechanism.

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

1. **Sync is on a 7-day poll** (parity with Quay). The first poll can take up to ~168 h. Watch progress in the logs:

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

2. **Docker Hub caveat:** the `ayufan/proxmox-backup-server` entry polls `docker.io` (Quay did the same). zot's docs advise against *catalog*-wide polling of Docker Hub; a single named repo at 7-day intervals is fine, but if you see rate-limit errors in the zot logs, change that entry to `"onDemand": true` (pull-through) — one line.

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
