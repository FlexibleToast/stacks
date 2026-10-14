# Forgejo

Self-hosted Git service with built-in CI, migrated to a Komodo stack. Three services:

- `forgejo` — the Forgejo instance (built-in SQLite, no DB sidecar). Web on `${FORGEJO_HTTP_PORT}`, SSH on `${FORGEJO_SSH_PORT}`.
- `forgejo-runner` — standard Gitea/Forgejo runner (`gitea/runner:${RUNNER_VERSION}`). Labels `ubuntu-latest`, `ubuntu-22.04`, and `ubuntu-20.04`; jobs run in their respective Ubuntu job images on the **host** Docker daemon (socket mounted). Not privileged.
- `forgejo-runner-dind` — docker-in-docker runner (`gitea/runner:${RUNNER_VERSION}-dind`). Label `dind`; jobs run in the same `gitea/runner-images:ubuntu-latest` but on a **bundled** dockerd (isolated from the host daemon). Privileged.

## How the runner images work (two layers)

There are two distinct images and they are easy to confuse:

1. **The runner image** (`gitea/runner:3` / `gitea/runner:3-dind`) is the container that runs the `gitea-runner` process. It is alpine-based and **never runs workflow steps** — it only polls Forgejo and spawns job containers. Its flavor decides how a Docker daemon is made available to jobs: the basic flavor uses the host daemon via the mounted socket, the `-dind` flavor bundles its own dockerd.

2. **The job image** is the container your workflow steps actually run in. In runner v3 this is not a compose field — it is carried by the label's `docker://<image>` argument. A label with no schema defaults to the `host` schema, which runs job steps directly inside the alpine runner container (apk), the wrong environment for `ubuntu-latest` workflows. The old `gitea/act_runner` image had a built-in name→image map, so bare labels like `ubuntu-latest` worked there; `gitea/runner` v3 has no such map, so the `docker://` schema is mandatory. The labels are written explicitly:

   ```
   ubuntu-latest:docker://gitea/runner-images:ubuntu-latest    (forgejo-runner)
   ubuntu-22.04:docker://gitea/runner-images:ubuntu-22.04      (forgejo-runner)
   ubuntu-20.04:docker://catthehacker/ubuntu:act-20.04         (forgejo-runner)
   dind:docker://gitea/runner-images:ubuntu-latest             (forgejo-runner-dind)
   ```

   `gitea/runner-images` are the official, recommended Gitea/Forgejo job images (`ubuntu-latest` is Ubuntu 24.04). The `20.04` label uses `catthehacker/ubuntu:act-20.04` because `gitea/runner-images` stopped building Ubuntu 20.04.

## Why two runners

Both run jobs in the same Ubuntu job image, but the **daemon the job talks to** differs:

| | `forgejo-runner` | `forgejo-runner-dind` |
|---|---|---|
| Runner image | `gitea/runner:3` | `gitea/runner:3-dind` |
| Daemon the job uses | host daemon (shared socket) | bundled dockerd (isolated) |
| Privileged | no | yes |
| Job can run docker | yes (via host daemon) | yes (via bundled daemon) |
| Security | job can reach the host daemon | job cannot reach the host daemon |
| Image reuse | reuses host image store (fast) | own image cache (re-pulls, then cached) |

The host-daemon runner is faster and shares the host image cache; the dind runner isolates each job in its own daemon, which is safer for untrusted CI code. Pick per workflow with `runs-on: ubuntu-latest` or `runs-on: dind`. Both are kept on purpose: the cost when idle is negligible, and the dind runner is the safe path for any workflow you don't fully control.

## One-time migration

The existing standalone `forgejo` container on Unraid uses the same data volume path, so no data migration is needed — only config:

1. **Generate runner registration tokens.** In Forgejo, create two CI runners (or two registration tokens). The labels are applied by the stack via `GITEA_RUNNER_LABELS` / `GITEA_RUNNER_DIND_LABELS`, so the runners just need valid tokens.
2. **Add the secrets to the secrets repo.** Put both tokens in the private Komodo/forgejo secrets repo: `FORGEJO_RUNNER_REGISTRATION_TOKEN` and `FORGEJO_RUNNER_DIND_REGISTRATION_TOKEN`. They are referenced in `komodo-resources/resources.toml` as `[[FORGEJO_RUNNER_REGISTRATION_TOKEN]]` / `[[FORGEJO_RUNNER_DIND_REGISTRATION_TOKEN]]`.
3. **Remove the old container.** Stop and delete the standalone `forgejo` Unraid container (do **not** delete `/mnt/user/appdata/forgejo` — the stack reuses it). Remove its `frontend` network attachment and free ports `222`/`3030`. The stack runs as `FORGEJO_USER_UID=99` / `FORGEJO_USER_GID=100`, matching the existing data ownership, so no re-owning is needed. If a previous runner registration exists under these names, delete it in Forgejo admin → CI runners first.
4. **Deploy.** Push to `main`; CI validates, syncs, and deploys the `forgejo` run_directory.
5. **Verify.** Confirm both runners appear under Forgejo admin → CI runners with labels `ubuntu-latest` and `dind`. Then run a throwaway workflow that, on `runs-on: ubuntu-latest` and `runs-on: dind`, runs `cat /etc/os-release` (must report Ubuntu 24.04, not Alpine) and `docker version` (proves the daemon is reachable; on the dind runner it must report the bundled daemon, not the host).

## Notes

- The dind runner is `privileged: true` — required for docker-in-docker. Job images are cached in `${CONTAINER_DATA}/forgejo-runner-dind/docker` so they persist across container recreation.
- A podman-in-docker runner was intentionally not included; `gitea/runner` has no first-class podman image. Revisit if/when a supported podman runner image exists.
