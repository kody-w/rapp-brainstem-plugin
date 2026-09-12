# Privacy

RAPP Brainstem receives the GitHub access token supplied by the host only for
the duration necessary to authenticate the request and invoke GitHub Copilot
for that user. Tokens are not written to application logs or persistent
storage.

The service stores Brainstem session state under the authenticated user's
immutable GitHub user ID. It does not sell personal data or use one user's
content to answer another user's requests.

The default memory agents persist explicitly saved memories across conversations
in that user's agent storage. Installed RAR source files and their integrity
receipts are stored under the same user's state directory. Removing the plugin
or revoking GitHub access does not erase this state; the gateway operator must
remove the user's stored data when deletion is requested.

Hacker News and the public RAR catalog are fetched without forwarding GitHub
credentials. RAR search terms are filtered locally. Additional installed agents
are trusted executable Python, not sandboxed extensions, and may make their own
network requests; review their source and data handling before installing.

Users can revoke access from their GitHub application settings and remove the
plugin from Microsoft Copilot Cowork.
