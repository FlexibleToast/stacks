# Stacks

Komodo infrastructure-as-code for deploying Docker Compose stacks across 5 servers. Config is in `komodo-resources/resources.toml`; secrets live in a private forgejo repo synced via Komodo.

## Commands

- Validate all stacks: `make validate` (runs `scripts/validate.py`)
- Deploy changed stacks: `./scripts/deploy.py` (dry-run when no API creds set)
- Single check: `python3 -c "import tomllib; tomllib.load(open('komodo-resources/resources.toml','rb'))"`

## Testing

Before finishing any task, run `make validate`. It checks YAML syntax, TOML syntax, docker compose config (with dummy env vars), and cross-references `linked_repo` against the current branch. Fix any errors before considering the task complete.

## Repo Structure

```
stack-dir/              # One per service (e.g. adguard/, vaultwarden/)
  compose.yaml          # Required — main service definition
  network.yaml          # Optional — network attachments
  ports.compose.yaml    # Optional — port mappings
  mounts.compose.yaml   # Optional — volume mounts
  {server}.compose.yaml # Optional — server-specific overrides
komodo-resources/
  resources.toml        # Central config: servers, stacks, builds, repos, procedures
scripts/
  validate.py           # CI validation
  deploy.py             # Auto-deploy on push to main
.github/workflows/
  validate.yml          # CI: validate → sync → deploy
.opencode/skills/       # Reusable agent skills for common workflows
```

## Stack Conventions

```
services:
  adguardhome:
    image: adguard/adguardhome:${ADGUARD_VERSION}
    container_name: adguardhome
    environment:
      TZ: ${TZ:-America/Chicago}
    volumes:
      - ${CONTAINER_DATA}/adguard/adguard-home/workingdir:/opt/adguardhome/work
    restart: ${SVC_RESTART:-unless-stopped}
```

- No `version` key in compose files
- No comments in compose files
- `${SVC_RESTART:-unless-stopped}` as restart policy
- Container name matches service name
- `Containerfile`, never `Dockerfile`

### Naming

Stacks in `resources.toml` follow `{service}-{server}`:

| Server | Suffix | CONTAINER_DATA |
|---|---|---|
| Unraid | `-unraid` | `/mnt/user/appdata` |
| Docker Oracle | `-oracle` | `/srv/container-data` |
| Bookview MicroOS | `-brookview` | `/srv/container-data` |
| Container Pi4 1 | `-container-pi` | `/srv/container-data` |
| NAS II (TrueNAS) | `-nas-ii` | `/mnt/pool-0/container-data` |

### resources.toml Entry

```toml
[[stack]]
name = "ddns-updater-brookview"
tags = ["brookview", "sync"]
[stack.config]
server = "Bookview MicroOS"
linked_repo = "stacks"
run_directory = "ddns-updater"
environment = """
  CONTAINER_DATA = /srv/container-data
  DDNS_UPDATER_VERSION = latest
"""
```

- `linked_repo` = `"stacks"` on main branch, `"kerbol-next"` on dev branch
- Secrets use `[[SECRET_NAME]]` placeholder syntax — never put real values here

### Networks

| Network | external | Use |
|---|---|---|
| `frontend` | true | Web-facing services |
| `backend` | false | Stack-internal only |
| `ai` | true | AI/ML services |
| `pangolin` | true | Reverse proxy/VPN |

## Boundaries

- Never use git to commit or push code — this repo is managed via GitHub UI and CI
- Never modify `.env.test` without adding a matching dummy variable for validation
- Don't modify `.github/` or `scripts/` without understanding the CI pipeline
- Don't add `version` to compose files, don't add comments to compose files

## CI/CD

Every push to `main` runs: `validate` (syntax + compose + cross-ref) → `sync` (Komodo resource sync) → `deploy` (API trigger for changed stacks). Only stacks whose `run_directory` changed in the diff are deployed.