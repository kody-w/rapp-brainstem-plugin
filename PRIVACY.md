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
