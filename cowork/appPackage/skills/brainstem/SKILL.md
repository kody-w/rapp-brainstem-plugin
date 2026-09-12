---
name: brainstem
description: |
  Uses the user's RAPP Brainstem for persistent memory, Hacker News, RAR agent
  discovery and installation, and installed RAPP capabilities. Use when the
  user says "use my Brainstem", "ask my Brainstem", "use RAPP", asks what
  Brainstem remembers, or requests a RAR agent.
license: MIT
metadata:
  author: RAPP
  version: "0.1.0"
---

# RAPP Brainstem

1. Call `brainstem_status` before the first Brainstem operation.
2. If GitHub is not connected, tell the user to connect their GitHub account.
3. Call `brainstem` with the user's request in plain English.
4. Continue with the returned session ID when the task is conversational.
5. Never ask the user to paste a GitHub token.
6. Never claim success when Brainstem reports an agent or authentication error.
7. The defaults are ContextMemory, ManageMemory, HackerNews, and RARRemoteAgent.
   Use the capability list from `brainstem_status`, not an assumed agent count.
8. Memory belongs to the signed-in user and survives a new conversation. Never
   ask for a user GUID. Save memories only when the user asks.
9. RAR search and inspection do not authorize installation. Pass an installation
   request only after the user explicitly chooses the exact package. Registry
   agents are trusted executable Python, not sandboxed extensions.
10. New agents load on the next `brainstem` request. Check `brainstem_status`
    afterward and surface `agentErrors`; do not claim a failed agent is ready.
