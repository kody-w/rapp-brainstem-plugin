# RAPP Brainstem Plugin

Use your RAPP Brainstem through your own GitHub Copilot account in Microsoft
Copilot Cowork and other Agent Skills hosts.

The plugin has one public product surface: **RAPP Brainstem**. Cowork performs
GitHub OAuth and stores each user's GitHub token in the Microsoft Enterprise
Token Store. The remote MCP gateway receives that token, resolves the
authenticated GitHub user, and creates an isolated GitHub Copilot SDK session
for that user.

```text
Cowork
  -> GitHub OAuth
  -> Microsoft Enterprise Token Store
  -> HTTPS MCP gateway
  -> per-user Copilot SDK session
  -> RAPP soul + drop-in agents
```

## Out-of-box agents

Brainstem starts with the standard RAR agents, not a greeting demo:

| File | Capability |
| --- | --- |
| `agents/basic_agent.py` | The actual shared `BasicAgent` class every agent inherits from; not a callable tool. |
| `agents/context_memory_agent.py` | `ContextMemory`: recall saved facts, preferences, insights, and tasks. |
| `agents/manage_memory_agent.py` | `ManageMemory`: save memories across conversations and restarts. |
| `agents/hacker_news_agent.py` | `HackerNews`: fetch current top stories and source links without an API key. |
| `agents/rar_remote_agent.py` | `RARRemoteAgent`: browse, search, inspect, list, and install RAR packages. |

The first four files are unmodified MIT-licensed packages from
[RAR](https://github.com/kody-w/RAR), pinned by revision and published SHA-256 in
`agents/defaults.lock.json`. The RAR client is a small hosted adapter for the
registry's public static API, not the desktop client's ambient-credential and
filesystem setup flow. Both `from agents.basic_agent import BasicAgent` and
`from basic_agent import BasicAgent` resolve to the same class.

Try these through the existing `brainstem` tool:

- "Remember that this project's goal is a useful daily project brief."
- "What do you remember about this project?"
- "Show me the top five Hacker News stories."
- "Search RAR for project summaries."
- "Install the RAR agent @publisher/exact_name." Use a name returned by search.

`brainstem_status` lists the actual loaded capabilities and any load errors.
Memory is bound to the authenticated GitHub user, not a model-supplied user ID
or conversation ID. The original agents use a host-supplied
`utils.storage_factory` compatibility layer backed by transactional SQLite
storage under `RAPP_STATE_PATH/<github-id>/agent-storage.sqlite3`. Concurrent
memory writes are serialized; signing out or changing conversations does not
erase memories.

### Adding agents from RAR

RAR discovery is public and needs no extra token. Search terms are matched
locally against its catalog; the gateway does not forward GitHub credentials
to RAR or Hacker News. Installation requires an explicit user request
(`action="install"`, `agent_name="@publisher/name"`, `confirm=true`).

Downloads must match the published `sha256-lf-v1` hash and manifest and pass
an import/schema check before admission. Source and its receipt are installed
together under `RAPP_STATE_PATH/<github-id>/agents/`, never the shared default
directory. The next Brainstem request hot-loads that user's new tools without
a server restart. Every subsequent load rechecks the source hash. A broken or
modified package is disabled with an explicit `agentErrors` entry; the defaults
remain usable.

This client supports public, standalone `BasicAgent` Python packages. Private
stubs and packages requiring other RAR packages are rejected explicitly. Python
dependencies must be provided by the gateway operator; agents never trigger an
automatic `pip install` through this client. Existing versions and default
capabilities are not silently overwritten.

**Trusted-code deployment:** installing a RAR agent executes its Python code.
This gateway follows the personal, trusted-registry model, not a Python sandbox.
Per-user storage prevents accidental state mixing but cannot contain malicious
Python. Only expose installation and agent execution to trusted users; do not
offer arbitrary registry installs to mutually untrusted tenants.

### Maintaining the defaults

Verify the vendored files offline:

```bash
python scripts/sync_rar_agents.py --check
```

To adopt reviewed upstream versions, update the revision, versions, and hashes
in `agents/defaults.lock.json`, then run `python scripts/sync_rar_agents.py`.
The script verifies every download before replacing any file. Do not hand-edit
the locked source files; host-specific behavior belongs in the gateway's
compatibility layer.

## Repository layout

- `src/rapp_brainstem_gateway/` - authenticated Streamable HTTP MCP gateway.
- `agents/` - bundled RAR defaults, their source lock, and the hosted RAR adapter.
- `soul.md` - Brainstem system instructions.
- `plugin/` - portable Claude/OpenPlugin source package.
- `cowork/appPackage/` - Microsoft 365 Cowork package source.
- `scripts/package_cowork.py` - validates and creates the uploadable ZIP.
- `infra/` - Azure Container Apps deployment assets.

## Local development

Python 3.12 is recommended.

The Python distribution includes the default agents, their lock file, and the
default soul, so it also works outside a source checkout. A workspace `soul.md`
takes precedence; an explicit `RAPP_SOUL_PATH` is always honored, including
reporting an error if that configured file is missing.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m copilot download-runtime
uvicorn rapp_brainstem_gateway.app:app --host 127.0.0.1 --port 7071
```

Health is anonymous:

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
and local Copilot credentials.

## Cowork package

Generate icons and an uploadable package:

```bash
python scripts/package_cowork.py \
  --mcp-url https://YOUR-GATEWAY.example/mcp \
  --oauth-reference-id YOUR-MICROSOFT-OAUTH-CONFIG-ID
```

The ZIP is written to `dist/rapp-brainstem-cowork.zip`. Upload it from
**Cowork -> Customize -> Plugins -> Add plugin**.

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

Authenticate to the intended personal Azure subscription, then:

```bash
./infra/deploy.sh --subscription YOUR_SUBSCRIPTION_ID
```

The script creates a resource group, Azure Container Registry, Container Apps
environment, and an externally accessible HTTPS Container App. It never
configures a service-wide GitHub token.

## Security model

- GitHub tokens are never written to disk or logs.
- GitHub's `/user` API establishes the immutable numeric user ID.
- Session IDs are namespaced and HMAC-derived from that user ID.
- User-provided IDs never select another user's state.
- Copilot SDK logged-in-user fallback is disabled.
- The runtime receives only the authenticated request user's token.
- Public MCP calls are capped below Cowork's 30-second deadline.
- RAR source hashes are checked before installation and every subsequent load.
- Memory context and registry install paths are selected by authenticated identity.
- Installed Python is trusted operator code, not a tenant security boundary.

## License

MIT
