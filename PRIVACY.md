# Privacy

RAPP Brainstem receives the GitHub access token supplied by the host only for
the duration necessary to authenticate the request and invoke GitHub Copilot
for that user. Tokens are not written to application logs or persistent
storage.

The service stores Brainstem session state under the authenticated user's
immutable GitHub user ID. It does not sell personal data or use one user's
content to answer another user's requests.

Users can revoke access from their GitHub application settings and remove the
plugin from Microsoft Copilot Cowork.

## RAPP Work tools

RAPP Work tools use the separately installed canonical `rapp-work` CLI. The
gateway authenticates the MCP request with the existing GitHub bearer flow but
does not pass that bearer token, GitHub tokens, cloud credentials, passwords,
SSH agent settings, or other ambient credential environment variables to the
CLI.

RAPP Work execution is offline-only by default and in this release cannot be
switched online. Inputs are limited to explicitly configured filesystem roots.
GitHub authentication alone does not grant access: the user's immutable
numeric GitHub ID must have an explicit owner allowlist or principal-to-root
grant. The gateway rejects unauthorized principals before probing the SDK or
filesystem. Anonymous health and authenticated status report only readiness
booleans and never disclose configured roots or principal mappings.
Read-only operations inspect local workspace metadata. Scaffold, update, and
migrate return a dry-run plan first and write only after explicit approval of
the exact current plan digest. Reviewed plans are retained only in a bounded,
expiring in-memory cache tied to the authenticated GitHub user and normalized
operation inputs. During apply, a short-lived plan file is stored under the
gateway state directory with owner-only permissions and removed after the CLI
exits.

One absolute request deadline includes authentication, the SDK compatibility
probe, and the requested operation. A subprocess receives only the time
remaining on that deadline.
