# Trek

AI-powered trip planner that turns travel ideas into detailed, shareable itineraries. Upstream: https://github.com/liketrek/TREK

- Image: `mauriceboe/trek:${TREK_VERSION}` (`trek/compose.yaml`)
- Web UI: container port `3000`; WebSocket at `/ws` (same port); health check at `/api/health`
- No host ports are published — Trek is reached only through Pangolin (Traefik)
- Joins the external `frontend` network (`trek/network.yaml`)
- Deployed on Unraid via the `trek-unraid` stack in `komodo-resources/resources.toml`

## Pangolin Setup

1. **Deploy.** Push the changes to `main`. CI (`validate` → `sync` → `deploy`) deploys the `trek-unraid` stack via Komodo — only stacks whose `run_directory` changed in the diff are deployed.

2. **Create the route in Pangolin.** Pangolin (`fosrl/pangolin`, UI at `https://pangolin.mcdade.app`) manages Traefik v3 routes. Routes are created manually in the web UI — there are no docker labels for routing in this repo.
   - Host: `trek.mcdade.app` (one subdomain per service is the house convention)
   - Backend: `trek` container, port `3000`, reachable on the `frontend` network
   - TLS is automatic (Let's Encrypt + Cloudflare DNS-01); nothing per-stack is needed.

3. **First-boot admin.** Either:
   - set `ADMIN_EMAIL` + `ADMIN_PASSWORD` **together** in the `trek-unraid` environment block (they must be set as a pair), or
   - read the admin password from the container log (default admin user: `admin@trek.local`).

4. **`APP_URL` / `FORCE_HTTPS` (optional).** If you enable `FORCE_HTTPS` or want the app to know its public URL, set `APP_URL=https://trek.mcdade.app` (and optionally `FORCE_HTTPS=true`). These are **not** currently in `trek/compose.yaml` — add them there plus to the `trek-unraid` environment block in `komodo-resources/resources.toml`, then run `make validate` and redeploy.

5. **WebSocket.** Traefik v3 upgrades normal HTTP routes to WebSocket automatically; no explicit WS config exists anywhere in this repo, so no extra step is expected for `/ws`.
   ⚠️ verify — no existing stack in this repo demonstrates a WebSocket route through Pangolin. If real-time sync fails, check the Pangolin route config.

## Authentik SSO (optional)

Authentik runs at `https://auth.mcdade.app`. Creating the application is a **manual** step in the Authentik admin UI — there is no automation for it in this repo.

1. **Create the Authentik application.**
   - Application with an OpenID Connect provider, slug `trek`
   - Flow: authorization code + PKCE
   - Issuer: `https://auth.mcdade.app/application/o/trek/`
   - Discovery URL: `https://auth.mcdade.app/application/o/trek/.well-known/openid-configuration`
   - Register the redirect URI.
     ⚠️ verify — Trek's exact OIDC callback path is not documented in this repo. Confirm it from Trek's own SSO setup screen/wiki before registering. Likely `https://trek.mcdade.app/api/auth/oidc/callback` (unverified).
   - Generate a token (client credentials) for the client ID and client secret.

2. **Add the secret to the secrets repo.** Put the client secret in the private Komodo/forgejo secrets repo. Reference it in `komodo-resources/resources.toml` as `[[TREK_OIDC_CLIENT_SECRET]]`. The client ID may be a literal (romm/mcphub pattern) or a `[[TREK_OIDC_CLIENT_ID]]` placeholder.

3. **Add the env vars to `trek/compose.yaml`.** Example:

   ```yaml
       environment:
         TZ: ${TZ:-America/Chicago}
         LOG_LEVEL: ${LOG_LEVEL:-info}
         ENCRYPTION_KEY: ${TREK_ENCRYPTION_KEY}
         APP_URL: https://trek.mcdade.app
         OIDC_ISSUER: https://auth.mcdade.app/application/o/trek/
         OIDC_DISCOVERY_URL: https://auth.mcdade.app/application/o/trek/.well-known/openid-configuration
         OIDC_CLIENT_ID: ${TREK_OIDC_CLIENT_ID}
         OIDC_CLIENT_SECRET: ${TREK_OIDC_CLIENT_SECRET}
         OIDC_DISPLAY_NAME: Authentik
         OIDC_SCOPE: openid email profile groups
         OIDC_ADMIN_CLAIM: groups
         OIDC_ADMIN_VALUE: your-admin-group
   ```

   - `OIDC_DISCOVERY_URL` is **required** for Authentik (non-standard discovery path).
   - `APP_URL` is required with OIDC and must match the redirect URI host.
   - Use `OIDC_SCOPE=openid email profile groups` for group-based admin; with the default scope (`openid email profile`) drop `OIDC_ADMIN_CLAIM`/`OIDC_ADMIN_VALUE`.
   - `OIDC_ONLY=true` restricts login to SSO only (optional).

4. **Add the values to `komodo-resources/resources.toml`** in the `trek-unraid` `environment` block, e.g.:

   ```
   TREK_OIDC_CLIENT_ID = [[TREK_OIDC_CLIENT_ID]]
   TREK_OIDC_CLIENT_SECRET = [[TREK_OIDC_CLIENT_SECRET]]
   ```

5. **Add dummies to `.env.test`** for any new non-secret vars if validation requires them, then run `make validate` and fix any errors.

6. **Redeploy.** Push to `main`; CI redeploys the changed `trek` stack.

## Secrets required

| Secret | Where referenced | Required | Generate |
|---|---|---|---|
| `TREK_ENCRYPTION_KEY` | `[[TREK_ENCRYPTION_KEY]]` in `resources.toml`; dummy in `.env.test` | Yes | `openssl rand -hex 32` |
| `TREK_OIDC_CLIENT_SECRET` | `[[TREK_OIDC_CLIENT_SECRET]]` in `resources.toml` | Only with SSO | Generate in the Authentik app (token/client credentials) |
