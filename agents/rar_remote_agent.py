"""The hosted RAR discovery/install surface; registry Python runs as trusted code."""

from utils.storage_factory import get_storage_manager

from agents.basic_agent import BasicAgent
from rapp_brainstem_gateway.rar import RARClient


class RARRemoteAgent(BasicAgent):
    def __init__(self):
        super().__init__(
            name="RARRemoteAgent",
            metadata={
                "name": "RARRemoteAgent",
                "description": (
                    "Discover, search, inspect and install single-file Python agents from RAR, "
                    "the RAPP Agent Registry. Public discovery needs no extra credentials. "
                    "Install only when the user explicitly requests that agent: it is trusted "
                    "executable Python, not a sandboxed plugin. Installs are SHA-256 verified "
                    "and available to this user on the next Brainstem request."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["discover", "search", "get_info", "install", "list_installed"],
                        },
                        "query": {"type": "string", "description": "Keywords for search."},
                        "agent_name": {
                            "type": "string",
                            "description": "Exact @publisher/name returned by RAR.",
                        },
                        "category": {"type": "string"},
                        "tier": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "True only when the user explicitly requested installation."
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        )
        self._client = RARClient(get_storage_manager())

    def perform(self, **kwargs):
        return self._client.perform(**kwargs)
