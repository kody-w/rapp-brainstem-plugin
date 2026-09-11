from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .errors import AuthenticationError


@dataclass(frozen=True)
class GitHubIdentity:
    id: str
    login: str


@dataclass
class _CacheEntry:
    identity: GitHubIdentity
    expires_at: float


class GitHubAuthenticator:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        cache_ttl_seconds: float = 60,
    ) -> None:
        self._settings = settings
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}

    @staticmethod
    def bearer_token(authorization: str | None) -> str:
        if not authorization:
            raise AuthenticationError("Connect your GitHub account to use Brainstem.")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("A valid GitHub bearer token is required.")
        return token.strip()

    async def identify(self, token: str) -> GitHubIdentity:
        cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return cached.identity

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._settings.github_api_version,
            "User-Agent": "rapp-brainstem-gateway",
        }
        if self._client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self._settings.github_api_url}/user",
                    headers=headers,
                )
        else:
            response = await self._client.get(
                f"{self._settings.github_api_url}/user", headers=headers
            )

        if response.status_code != 200:
            raise AuthenticationError("GitHub authorization is invalid or expired.")

        data = response.json()
        user_id = data.get("id")
        login = data.get("login")
        if not isinstance(user_id, int) or not isinstance(login, str) or not login:
            raise AuthenticationError("GitHub returned an incomplete user identity.")

        identity = GitHubIdentity(id=str(user_id), login=login)
        self._cache[cache_key] = _CacheEntry(
            identity=identity,
            expires_at=now + self._cache_ttl_seconds,
        )
        return identity
