from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    agents_path: Path
    soul_path: Path
    state_path: Path
    github_api_url: str
    github_api_version: str
    request_timeout_seconds: float
    model: str
    session_secret: str

    @classmethod
    def from_env(cls) -> Settings:
        root = Path(os.getenv("RAPP_ROOT", Path.cwd())).resolve()
        session_secret = os.getenv("RAPP_SESSION_SECRET", "").strip()
        if not session_secret:
            session_secret = "development-only-change-me"
        return cls(
            root=root,
            agents_path=Path(os.getenv("RAPP_AGENTS_PATH", root / "agents")).resolve(),
            soul_path=Path(os.getenv("RAPP_SOUL_PATH", root / "soul.md")).resolve(),
            state_path=Path(os.getenv("RAPP_STATE_PATH", root / "copilot-home")).resolve(),
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            github_api_version=os.getenv("GITHUB_API_VERSION", "2022-11-28"),
            request_timeout_seconds=float(os.getenv("RAPP_REQUEST_TIMEOUT_SECONDS", "25")),
            model=os.getenv("RAPP_MODEL", "gpt-5.4"),
            session_secret=session_secret,
        )
