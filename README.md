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

## Repository layout

- `src/rapp_brainstem_gateway/` - authenticated Streamable HTTP MCP gateway.
- `agents/` - hot-loaded RAPP `*_agent.py` files.
- `soul.md` - Brainstem system instructions.
- `plugin/` - portable Claude/OpenPlugin source package.
- `cowork/appPackage/` - Microsoft 365 Cowork package source.
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

## License

MIT
