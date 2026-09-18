# RAPP Brainstem Plugin

Use your RAPP Brainstem through your own GitHub Copilot account in Microsoft
Copilot Cowork and other Agent Skills hosts.

The plugin has two first-class surfaces: **RAPP Brainstem** and **RAPP Work**.
Cowork performs GitHub OAuth and stores each user's GitHub token in the
Microsoft Enterprise Token Store. The remote MCP gateway keeps that bearer
authentication for every tool. Brainstem creates an isolated GitHub Copilot
SDK session for the authenticated user; RAPP Work invokes a separately
installed canonical `rapp-work` 1.0.0 CLI without forwarding the token.

```text
Cowork
  -> GitHub OAuth
  -> Microsoft Enterprise Token Store
  -> HTTPS MCP gateway
     -> per-user Copilot SDK session -> RAPP soul + drop-in agents
     -> canonical rapp-work CLI/SDK -> local RAPP Workspaces
```

## Repository layout

- `src/rapp_brainstem_gateway/` - authenticated Streamable HTTP MCP gateway.
- `src/rapp_brainstem_gateway/rapp_work.py` - bounded canonical CLI adapter.
- `agents/` - hot-loaded RAPP `*_agent.py` files.
- `soul.md` - Brainstem system instructions.
- `plugin/` - portable Claude/OpenPlugin source package.
- `cowork/appPackage/` - Microsoft 365 Cowork package source.
- `RAPP_WORK_SDK_PIN.json` - required SDK/profile/version and accepted source pin.
- `RAPP_WORK_PLUGIN_RELEASE.json` - deterministic release inventory for static API pinning.
- `scripts/package_cowork.py` - validates and creates the uploadable ZIP.
- `infra/` - Azure Container Apps deployment assets.

## Local development

Python 3.12 is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m copilot download-runtime
uvicorn rapp_brainstem_gateway.app:app --host 127.0.0.1 --port 7071
```

### Configure the canonical RAPP Work CLI

`rapp-work` is intentionally not a PyPI dependency of this gateway. Install or
mount the canonical SDK separately. The gateway resolves it in this order:

1. the shell-style command prefix in `RAPP_WORK_COMMAND`; or
2. the gateway interpreter running `python -m rapp_work`.

The CLI must report SDK version `1.0.0` through `--version`. If it is absent or
incompatible, RAPP Work tools fail clearly; the gateway never substitutes its
own RAPP/1 implementation.

```bash
export RAPP_WORK_COMMAND="/opt/rapp-work/bin/python -m rapp_work"
export RAPP_WORK_ROOTS="/workspaces:/other/approved/root"
export RAPP_WORK_OWNER_IDS="12345678"
# Or scope immutable GitHub IDs to subsets of RAPP_WORK_ROOTS:
export RAPP_WORK_PRINCIPAL_ROOTS='{"12345678":["/workspaces/alice"]}'
export RAPP_WORK_TIMEOUT_SECONDS=15
export RAPP_WORK_MAX_OUTPUT_BYTES=1048576
```

`RAPP_WORK_ROOTS` uses the platform path separator. Relative tool paths resolve
under its first entry. Every path, including every discovery root and migration
endpoint, must resolve inside an allowed root without a symlink escape.
Configured entries must be absolute. Authentication alone never authorizes
RAPP Work: the immutable numeric GitHub user ID must appear in
`RAPP_WORK_OWNER_IDS` or `RAPP_WORK_PRINCIPAL_ROOTS`. A principal mapping takes
precedence over the owner allowlist and limits that principal to its mapped
roots.

The six explicit tools map directly to the canonical CLI:

| MCP tool | Canonical operation | Behavior |
| --- | --- | --- |
| `rapp_work_status` | `status` | Sanitized readiness without root disclosure |
| `rapp_work_verify` | `verify` | Read-only canonical verification |
| `rapp_work_discover` | `discover` | Bounded, inert metadata discovery |
| `rapp_work_scaffold` | `scaffold` | Dry-run plan; explicit apply only |
| `rapp_work_update` | `update` | Dry-run plan; explicit apply only |
| `rapp_work_migrate` | `migrate` | Source-preserving dry-run plan; explicit apply only |

The integration is offline-only. Mutators reject plans that target `.git`,
credential paths, `soul.md`, or `brainstem.py`. Reviewed plans are held in a
bounded 15-minute in-memory cache bound to the authenticated GitHub identity,
operation, and exact normalized inputs. Apply requires `apply: true` with the
exact 64-character `planDigest`. The complete reviewed plan is passed back to
the CLI through a private, short-lived state file and removed after execution.

Subprocesses receive a credential-stripped environment and a combined output
limit. One monotonic request deadline covers request parsing, GitHub
authentication, the SDK version probe, plan handling, and the operation; each
subprocess receives only the budget remaining at launch. Duplicate JSON
members, non-standard JSON, nonzero exits, missing output, and incompatible
versions are refusals.

Health is anonymous. It returns HTTP 200 only when principal authorization is
configured, workspace roots exist, and the pinned SDK passes its version probe.
It returns booleans only and never returns configured paths or principals:

```bash
curl http://127.0.0.1:7071/health
```

Every MCP request requires a GitHub user token:

```bash
curl -X POST http://127.0.0.1:7071/mcp \
  -H "Authorization: Bearer $COPILOT_GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

The service intentionally ignores ambient `gh`, `GH_TOKEN`, `GITHUB_TOKEN`,
cloud credentials, and local Copilot credentials. RAPP Work subprocesses do
not receive the authenticated user's bearer token.

## Cowork package

Generate icons and an uploadable package:

```bash
python scripts/package_cowork.py \
  --mcp-url https://YOUR-GATEWAY.example/mcp \
  --oauth-reference-id YOUR-MICROSOFT-OAUTH-CONFIG-ID
```

The ZIP is written to `dist/rapp-brainstem-cowork.zip`. Packaging validates
manifest versions, closed MCP schemas, gateway/Cowork tool parity, and
byte-identical Brainstem and RAPP Work skills before creating the archive.
Upload it from **Cowork -> Customize -> Plugins -> Add plugin**.

Release packaging has no fallback: it requires the exact canonical repository
and a pinned 40-character lowercase SDK commit. Regenerate and verify the
deterministic static-API descriptor after changing any release file:

```bash
python scripts/build_release_manifest.py --write
python scripts/build_release_manifest.py --check
```

If a future SDK revision has not yet received an accepted source commit,
developers may use `--allow-unreleased-sdk-pin-for-development` for manifest
and Cowork package validation, or
`RAPP_WORK_ALLOW_UNRELEASED_SDK_PIN_FOR_DEVELOPMENT=1` for wheel/sdist builds.
The development override requires an explicit `unreleased` pin status and is
rejected when `RAPP_RELEASE_BUILD=1`.

The OAuth registration must use the GitHub App client credentials and:

```text
Callback URL:          https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect
Authorization endpoint: https://github.com/login/oauth/authorize
Token endpoint:         https://github.com/login/oauth/access_token
Refresh endpoint:       https://github.com/login/oauth/access_token
```

Use **Any Microsoft 365 organization** and **Any Teams app** in the Microsoft
OAuth client registration.

### One-click GitHub App registration

GitHub's manifest flow can create the correctly configured GitHub App without
manually copying settings into Developer Settings. Expose the local callback
through a temporary HTTPS tunnel, then run:

```bash
python scripts/github_app_manifest_server.py \
  --public-url https://YOUR-TEMPORARY-TUNNEL
```

Open the printed URL and approve creation. The generated credentials are stored
at `~/.brainstem/github-app.json` with mode `0600`; secrets are never displayed
in the browser or written to the repository. Delete the file after registering
the OAuth client in Microsoft.

## Azure deployment

After the SDK pin is final, the production image can be built directly:

```bash
SDK_COMMIT="$(python3 -c 'import json; print(json.load(open("RAPP_WORK_SDK_PIN.json"))["sourceCommit"])')"
docker build \
  --build-arg "RAPP_WORK_SOURCE_COMMIT=${SDK_COMMIT}" \
  --tag "rapp-brainstem:sdk-${SDK_COMMIT}" .
```

Authenticate to the intended personal Azure subscription, then:

```bash
./infra/deploy.sh \
  --subscription YOUR_SUBSCRIPTION_ID \
  --rapp-work-owner-id YOUR_NUMERIC_GITHUB_USER_ID
```

The script creates a resource group, Azure Container Registry, Container Apps
environment, separate state/workspace shares, and an externally accessible
HTTPS Container App. It builds an immutable image tag from the exact SDK
commit in `RAPP_WORK_SDK_PIN.json` (or the matching
`--rapp-work-source-commit` argument), installs the SDK only from that canonical
Git commit, configures the absolute SDK command and `/workspaces` root, and
waits for readiness. It never configures a service-wide GitHub token.

## Security model

- GitHub tokens are never written to disk or logs.
- GitHub's `/user` API establishes the immutable numeric user ID.
- Session IDs are namespaced and HMAC-derived from that user ID.
- User-provided IDs never select another user's state.
- Copilot SDK logged-in-user fallback is disabled.
- The runtime receives only the authenticated request user's token.
- The canonical RAPP Work CLI receives no bearer or ambient credential values.
- RAPP Work requires an immutable GitHub principal authorization grant in
  addition to authentication.
- RAPP Work is offline-only and constrained to that principal's configured
  filesystem roots.
- Mutating operations require a current reviewed plan and exact plan digest.
- Protected Brainstem, Git, and credential paths are denied before apply.
- Anonymous health and authenticated status expose readiness without paths.
- Public MCP calls are capped below Cowork's 30-second deadline.

## RAPP Work release pin

This plugin targets distribution `rapp-work` version `1.0.0` and profile
`rapp-work-sdk/1` at accepted canonical `kody-w/rapp-work` commit
`29ead23b21645f8d7682ee00414930ffa9ce0ca6`.
`RAPP_WORK_SDK_PIN.json` records that exact commit with status `pinned`, and
`RAPP_WORK_PLUGIN_RELEASE.json` records release status `final`. Release CI sets
`RAPP_RELEASE_BUILD=1`, so neither packaging nor the production image can use
an unreleased, noncanonical, or PyPI/latest SDK.

## License

MIT
