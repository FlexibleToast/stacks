# Dawarich

Self-hosted Google Timeline replacement: track location history, visualize it on an
interactive map, build trips, and share with family. Upstream: https://github.com/Freika/dawarich

- App image: `freikin/dawarich:${DAWARICH_VERSION}` (Rails web + a separate Sidekiq worker)
- Sidecars: `dawarich_db` (`postgis/postgis`, spatial queries), `dawarich_redis` (cache/queue)
- Web UI: container port `3000`; health check at `/api/v1/health`
- Joins the external `frontend` network (web) plus an internal `backend` network
  (web + all sidecars) — see [`unraid.network.yaml`](unraid.network.yaml)
- Deployed on Unraid via the `dawarich-unraid` stack in `komodo-resources/resources.toml`

> ⚠️ **Do not update automatically.** Upstream ships frequent, sometimes-breaking
> changes. Read the release notes before bumping `DAWARICH_VERSION`, and
> [back up your data](https://dawarich.app/docs/tutorials/backup-and-restore) first.
> The existing version-check pipeline will open a PR for each new tag; review it
> rather than auto-merging.

## First boot

1. **Deploy.** Push the changes to `main`. CI (`validate` → `sync` → `deploy`)
   deploys the `dawarich-unraid` stack via Komodo — only stacks whose
   `run_directory` changed in the diff are deployed.
2. **Create the first user.** The upstream `docker` compose sets a default
   `demo@dawarich.app` / `safepassword` admin, but that is a **development**
   convenience and is not wired into this stack's production env. In a production
   deploy (`RAILS_ENV=production`) there is no seeded user — create your account
   through the app after the reverse-proxy step, or add an initial admin per the
   upstream docs. Confirm the exact first-admin flow against the version you pin,
   since it is not documented in this repo.

## Pangolin (reverse proxy) setup

Everything is reached through Pangolin (Traefik). Routes are created manually in the
Pangolin UI — there are no docker routing labels in this repo.

1. **Create the route.**
   - Host: `dawarich.mcdade.app` (one subdomain per service is the house convention)
   - Backend: `dawarich` container, port `3000`, reachable on the `frontend` network
   - TLS is automatic (Let's Encrypt + Cloudflare DNS-01); nothing per-stack is needed.

2. **Tell the app its public host + protocol.** These are the two reverse-proxy
   env vars. Set them in the `dawarich-unraid` `environment` block in
   `komodo-resources/resources.toml` (they already exist as overridable vars in
   [`compose.yaml`](compose.yaml)):

   ```
   DAWARICH_APPLICATION_HOSTS = dawarich.mcdade.app
   DAWARICH_APPLICATION_PROTOCOL = https
   ```

   Defaults are `localhost,::1,127.0.0.1` / `http`, which only work for local
   access — override them for a proxied deployment, then run `make validate` and
   redeploy.

3. **Host port (optional).** [`compose.yaml`](compose.yaml) currently publishes
   `${DAWARICH_PORT:-3000}:3000` on the host. Since access is via Pangolin, you can
   remove the `ports:` block to avoid exposing 3000 on the LAN. If you keep it,
   confirm 3000 is free on the Unraid host or set `DAWARICH_PORT`.

## Authentik SSO (optional, wired via `oidc.unraid.compose.yaml`)

Dawarich has built-in generic **OIDC** login (works with Authentik, Authelia,
Keycloak, etc.). The compose side is already in place in
[`oidc.unraid.compose.yaml`](oidc.unraid.compose.yaml) — it sets the OIDC env vars
on **both** the `dawarich` and `dawarich-sidekiq` services (issuer, redirect URI,
provider name) and reads the credentials from `OIDC_CLIENT_ID` /
`OIDC_CLIENT_SECRET`, which the `dawarich-unraid` environment block in
`komodo-resources/resources.toml` provides as `[[DAWARICH_OIDC_CLIENT_ID]]` /
`[[DAWARICH_OIDC_CLIENT_SECRET]]` placeholders. Only the Authentik app and the
secrets are manual steps.

1. **Create the Authentik application** (manual, in the Authentik admin UI at
   `https://auth.mcdade.app`):
   - Application with an OpenID Connect provider, slug `dawarich`
   - Flow: authorization code (+ PKCE if you enforce it)
   - Issuer: `https://auth.mcdade.app/application/o/dawarich/`
     (standard discovery; no manual endpoints needed)
   - Redirect URI: `https://dawarich.mcdade.app/users/auth/openid_connect/callback`
     (this callback path is documented in Dawarich's `docker/.env.example`)
   - Copy the generated client ID and client secret (token/client credentials)

2. **Add the secrets to the secrets repo.** Put both values in the private
   Komodo/forgejo secrets repo: `DAWARICH_OIDC_CLIENT_ID` and
   `DAWARICH_OIDC_CLIENT_SECRET` (they are referenced in
   `komodo-resources/resources.toml` as `[[DAWARICH_OIDC_CLIENT_ID]]` /
   `[[DAWARICH_OIDC_CLIENT_SECRET]]`).

3. **Redeploy.** Push to `main`; CI redeploys the changed `dawarich` stack.

Optional behaviour flags (add to [`oidc.unraid.compose.yaml`](oidc.unraid.compose.yaml),
Dawarich env names):
- `OIDC_AUTO_REGISTER` — default `true`; set `false` to require pre-created accounts
- `ALLOW_EMAIL_PASSWORD_LOGIN` — default `true`; set `false` to enforce SSO-only sign-in
- `ALLOW_EMAIL_PASSWORD_REGISTRATION` — default `false` in self-hosted mode
- `OIDC_PKCE_ENABLED` — set `true` if your client enforces PKCE

## Secrets required

| Secret | Where referenced | Required | Generate |
|---|---|---|---|
| `DAWARICH_DB_PASSWORD` | `[[DAWARICH_DB_PASSWORD]]` in `resources.toml` | Yes | `openssl rand -hex 16` |
| `DAWARICH_SECRET_KEY_BASE` | `[[DAWARICH_SECRET_KEY_BASE]]` in `resources.toml` | Yes (production) | `openssl rand -hex 64` |
| `DAWARICH_OIDC_CLIENT_ID` | `[[DAWARICH_OIDC_CLIENT_ID]]` in `resources.toml` | Only with SSO | Copy from the Authentik app |
| `DAWARICH_OIDC_CLIENT_SECRET` | `[[DAWARICH_OIDC_CLIENT_SECRET]]` in `resources.toml` | Only with SSO | Generate in the Authentik app |

`DAWARICH_DB_PASSWORD` is shared by the app, the worker, and the Postgres
container. `DAWARICH_SECRET_KEY_BASE` is mandatory in production — the Rails app
refuses to boot in `RAILS_ENV=production` without it.

## Optional integrations

- **Immich / Photoprism:** Dawarich can import geodata from photos and render them
  on the map — configured in-app with credentials, no compose changes.
- **Reverse geocoding:** photon / Nominatim / Geoapify / LocationIQ — configured
  in-app (Settings → Instance) or pinned via `PHOTON_*` / `NOMINATIM_*` env vars.
- **Location tracking:** install the Dawarich iOS/Android app (or Overland,
  OwnTracks, GPSLogger, Home Assistant) and point it at
  `https://dawarich.mcdade.app`.
