 # Forgejo

Self-hosted Git service with built-in CI, migrated to a Komodo stack. Three services:

- `forgejo` — the Forgejo instance (built-in SQLite, no DB sidecar). Web on `${FORGEJO_HTTP_PORT}`, SSH on `${FORGEJO_SSH_PORT}`.
- `forgejo-runner` — standard Gitea/Forgejo runner. Shares the host Docker daemon via `/var/run/docker.sock`. Not privileged.
- `forgejo-runner-dind` — privileged docker-in-docker runner (`gitea/runner:3-dind`). Bundles its own dockerd; job images live in `${CONTAINER_DATA}/forgejo-runner-dind/docker`.

Runners register automatically on first start using the token in `GITEA_RUNNER_REGISTRATION_TOKEN` / `GITEA_RUNNER_DIND_REGISTRATION_TOKEN`. The `depends_on: service_healthy` gate waits for Forgejo's `forgejo admin ping` healthcheck before each runner attempts to register.

## One-time migration

The existing standalone `forgejo` container on Unraid uses the same data volume path, so no data migration is needed — only config:

1. **Generate runner registration tokens.** In Forgejo, create two CI runners (or two registration tokens): one labelled `docker`, one labelled `dind`. These labels match `GITEA_RUNNER_LABELS` / `GITEA_RUNNER_DIND_LABELS` in `resources.toml`.
2. **Add the secrets to the secrets repo.** Put both tokens in the private Komodo/forgejo secrets repo: `FORGEJO_RUNNER_REGISTRATION_TOKEN` and `FORGEJO_RUNNER_DIND_REGISTRATION_TOKEN`. They are referenced in `komodo-resources/resources.toml` as `[[FORGEJO_RUNNER_REGISTRATION_TOKEN]]` / `[[FORGEJO_RUNNER_DIND_REGISTRATION_TOKEN]]`.
3. **Remove the old container.** Stop and delete the standalone `forgejo` Unraid container (do **not** delete `/mnt/user/appdata/forgejo` — the stack reuses it). Remove its `frontend` network attachment and free ports `222`/`3030`. The stack runs as `FORGEJO_USER_UID=99` / `FORGEJO_USER_GID=100`, matching the existing data ownership, so no re-owning is needed.
4. **Deploy.** Push to `main`; CI validates, syncs, and deploys the `forgejo` run_directory. Verify both runners appear under Forgejo's admin → CI runners.

## Notes

- The dind runner is `privileged: true` — expected for docker-in-docker. Job images are cached in the `docker` sub-volume so they persist across container recreation.
- A podman-in-docker runner was intentionally not included; `gitea/runner` has no first-class podman image. Revisit if/when a supported podman runner image exists.
