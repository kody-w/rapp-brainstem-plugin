from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GITHUB_CREATE_URL = "https://github.com/settings/apps/new"
GITHUB_CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"
TEAMS_OAUTH_CALLBACK = "https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect"


def app_manifest(public_url: str) -> dict:
    return {
        "name": "RAPP Brainstem Cowork",
        "url": "https://github.com/kody-w/rapp-brainstem-plugin",
        "redirect_url": f"{public_url.rstrip('/')}/callback",
        "callback_urls": [TEAMS_OAUTH_CALLBACK],
        "description": "Use RAPP Brainstem through each user's GitHub Copilot account.",
        "public": True,
        "request_oauth_on_install": False,
        "default_permissions": {},
        "default_events": [],
    }


def exchange_manifest_code(code: str) -> dict:
    request = urllib.request.Request(
        GITHUB_CONVERSION_URL.format(code=urllib.parse.quote(code, safe="")),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "rapp-brainstem-plugin-setup",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub manifest conversion failed ({exc.code}): {detail}") from exc


def write_credentials(path: Path, credentials: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(credentials, output, indent=2)
        output.write("\n")
    path.chmod(0o600)


def create_handler(public_url: str, state: str, output: Path):
    manifest = json.dumps(app_manifest(public_url), separators=(",", ":"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._html(
                    f"""
                    <h1>Register RAPP Brainstem</h1>
                    <p>GitHub will create the OAuth app under your signed-in account.</p>
                    <form action="{GITHUB_CREATE_URL}?state={html.escape(state)}" method="post">
                      <input type="hidden" name="manifest" value="{html.escape(manifest)}">
                      <button type="submit">Create RAPP Brainstem GitHub App</button>
                    </form>
                    """
                )
                return
            if parsed.path != "/callback":
                self.send_error(404)
                return

            query = urllib.parse.parse_qs(parsed.query)
            received_state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            if not secrets.compare_digest(received_state, state) or not code:
                self.send_error(400, "Invalid GitHub manifest callback")
                return

            try:
                credentials = exchange_manifest_code(code)
                write_credentials(output, credentials)
            except RuntimeError as exc:
                self.send_error(502, str(exc))
                return

            client_id = html.escape(str(credentials.get("client_id", "")))
            self._html(
                f"""
                <h1>RAPP Brainstem GitHub App created</h1>
                <p>Client ID: <code>{client_id}</code></p>
                <p>The client secret was written to a local mode-0600 setup file,
                not displayed in the browser.</p>
                """
            )

        def log_message(self, format: str, *args) -> None:
            print(f"[github-app-setup] {format % args}")

        def _html(self, body: str) -> None:
            payload = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>RAPP Brainstem setup</title></head><body>"
                f"{body}</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GitHub App manifest setup callback")
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / ".brainstem" / "github-app.json",
    )
    args = parser.parse_args()

    state = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        create_handler(args.public_url, state, args.output.expanduser()),
    )
    print(f"Open {args.public_url.rstrip('/')}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
