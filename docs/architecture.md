# Implementation architecture

The cross-repository boundary is defined in [Launcher ↔ Harness Docker runtime contract](runtime-contract.md). It distinguishes the implemented v1 adapter from proposed future interfaces.

## Responsibility and state

- `app.py`: HTTP application, authenticated APIs, member/admin authorization, SPA hosting.
- `security.py`: Argon2 passwords, opaque hashed login sessions, CSRF and encrypted secrets.
- `services.py`: ownership, resource admission, idempotent operation submission.
- `worker.py`: single-host operation processing, lease recovery, actual/desired-state reconciliation.
- `templates.py`: immutable published template versions, per-space snapshots and effective resource limits.
- `runtime_types.py`: independent RuntimeSpec / RuntimeHandle adapter contract.
- `runtime.py`: pinned runzhliu Docker adapter, resource constraints, compatibility config rendering, native authentication and connection recovery.
- `proxy.py`: HTTP/streaming/WebSocket forwarding, runtime cookie injection, authorization rechecks.
- `files.py`: directory-descriptor file traversal; symlinks and special files rejected.
- `web/src`: React member and administrator flows.

The database is the source of desired state. Docker is the source of observed runtime state. Member configuration has monotonically increasing versions; each running container is labelled with the applied version.

## Lifecycle

1. The API verifies membership and locks the platform admission row.
2. It checks the global running limit including reservations, records desired state and creates an operation with a unique active workspace key.
3. The worker leases the operation and prepares the pinned environment.
4. It creates an instance network and resource-constrained container, then uses the native launch token on the server to acquire a Harness session cookie.
5. Only after an authenticated readiness check does it publish the runtime address and encrypted cookie.
6. Failures release the reservation, clean up runtime resources, preserve user storage and record a sanitized error.
7. Reconciliation restores proxy network membership and native authentication after control-container replacement without replacing the running user container. Lost containers become errors; temporary connection failures enter recovering and retry.
8. Expired operation leases are retried after worker recovery. Optional runtime deadlines enqueue ordinary stop operations without resetting the deadline during reconnection.

One host and one worker are intentional MVP constraints. The file lock is shared in the data root. PostgreSQL admission transactions plus the operation uniqueness constraint provide API-side concurrency safety. Docker names, workspace IDs and labels permit idempotent recovery.

MySQL 8.4 is also supported through PyMySQL. Tables use InnoDB, explicit VARCHAR lengths, TEXT for credentials/instructions, utf8mb4 binary collation, and DOUBLE for epoch timestamps. The connection uses READ COMMITTED: a request may have read its session and workspace before waiting on the admission lock, so MySQL's default repeatable-read snapshot would otherwise hide newly reserved capacity. Pre-ping and connection recycling handle stale pooled connections. Both databases retain the unique active-operation constraint.

PostgreSQL is the default for settings, generated environment files and `compose.yaml`. `compose.dev.yaml` additionally publishes PostgreSQL on localhost:55432 for host-run Python processes. MySQL remains available only through an explicit connection URL or the independent `compose.mysql.yaml`. Direct Python processes use `LAUNCHER_DATABASE_URL`. Changing the selected database does not migrate existing accounts, sessions, workspace metadata or operation history.

## Routing and identity

`PUBLIC_URL` can be an IP or existing domain with an arbitrary path prefix:

```text
<base>/                         React control UI
<base>/api/auth/*                identity and sessions
<base>/api/workspace             own workspace
<base>/api/workspaces/<id>/*      configuration, operations, files
<base>/api/admin/*               users, platform, lifecycle administration, audit
<base>/w/<id>/*                  authenticated native Harness proxy
```

Native HTTP and WebSocket requests retain the browser-facing authority required by Harness. The proxy supplies only the workspace's native cookie; platform cookies, authorization headers and native Set-Cookie headers never cross the wrong boundary. Launch query tokens are rejected at the browser-facing HTTP proxy. HTTP redirects are rewritten to the workspace mount.

The member's session and workspace ownership are checked before access and during streams. Administrator resource management does not grant content access. The UI runs on one origin; this is suitable only for the controlled runtime frontend, not arbitrary tenant-supplied HTML/JavaScript.

## NEW API integration

The administrator supplies the inference base URL and model catalog. Members supply their own inference token; it is encrypted in the DB and injected only into their runtime. No management or enterprise upstream credentials are shared with runtimes. Runtime model calls go directly to NEW API. The connection check reads `/models` without invoking inference.

Model credentials are optional for entering a workspace. Admission checks ownership and capacity without requiring a gateway, model or token. Until all three are configured, the managed model provider, default model and automatic session titles are disabled; users can save preferences and use the workspace UI. Saving model configuration and restarting enables those features. This does not supply free inference or a shared fallback credential.

A NEW API management/identity adapter can be added independently later. Current local login does not imply single sign-on with the gateway.

## Configuration and plugins

The pinned adapter appends a read-only Cordis overlay after the image's default overlay. It provides `llm-pi-ai`'s `team-gateway` route and the initial model, and toggles the built-in `session-title-llm` row. Personal instructions are mounted read-only at `$DSH_HOME/AGENTS.md`.

The managed template disables the native plugin-manager UI. This is a supported-product boundary, not a guarantee that an agent with command execution cannot alter its own writable environment. Plugin capability labels are descriptive, not an OS permission system.

## Networking

Each space gets a separate `/28` bridge subnet selected from a configurable pool. Only that runtime and the control proxy/worker join it. The runtime never receives the Docker Socket or database network. Local development publishes its native port to localhost for Docker Desktop compatibility; deployment uses container-to-container traffic.

These bridge networks allow outbound connectivity so the agent can use its configured gateway and development tools. Fine-grained destination allowlists and hostile tenant isolation require external enforcement and are not claimed by this MVP.

## Persistence and upgrades

The workspace file tree and Harness home survive all lifecycle operations. The instance is replaceable; data is not deleted as part of stopping/rebuilding. Generated files are kept outside the writable project/home roots and mounted read-only.

Schema bootstrap is currently SQLModel `create_all`; production upgrades need versioned migrations before changing persisted structures. Upgrading the runtime image requires checking upstream state compatibility, taking a stopped-space backup, running the smoke test and testing actual gateway/tool behavior. Do not automatically downgrade a migrated Harness home.

## Templates and versions

RuntimeTemplate rows are editable drafts until publication; published content is immutable and can only be enabled/disabled. New versions are separate rows. WorkspaceRuntime stores the desired snapshot, applied snapshot, pinned image ID, observed image identity and optional runtime deadline. New workspaces choose a published enabled template; retiring it does not invalidate existing snapshots. Resources are capped by both template defaults and member quotas.

Explicit template changes require an optimistic workspace configuration version and a matching administrator-declared data compatibility group. Admin upgrades combine a snapshot change with a queued rebuild. Images must already exist on the Docker host; publishing is an administrator approval, not an image build or compatibility certification. The worker never queries model providers to decide container readiness.

This release adds two tables and idempotently backfills old spaces; it does not ALTER existing account/workspace tables or migrate Harness home formats. Legacy spaces retain their owner resource limits and adopt the observed image when the worker reconnects. Future schema changes still require explicit migration design.
