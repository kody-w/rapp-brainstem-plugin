---
name: rapp-work
description: |
  Uses the canonical RAPP Work CLI/SDK to inspect, verify, discover, scaffold,
  update, or migrate RAPP Workspaces. Use for RAPP Work setup and maintenance,
  never to recreate RAPP/1 behavior in the host.
license: MIT
metadata:
  author: RAPP
  version: "0.2.1"
---

# RAPP Work

1. Start with `rapp_work_status`; it reports readiness without disclosing
   configured roots. Use `rapp_work_verify` for evidence and
   `rapp_work_discover` for inert discovery.
2. Keep `offline` true. This integration refuses online execution; never work
   around that boundary.
3. Treat `rapp_work_scaffold`, `rapp_work_update`, and `rapp_work_migrate` as
   dry-runs first. Show the canonical plan and its digest.
4. Apply only after the user explicitly approves that exact plan. Send
   `apply: true` with the unchanged `planDigest`.
5. Never ask for or pass credentials. Never work around a refused principal,
   root, symlink, protected path, missing CLI, failed verification, or changed
   plan.
6. The configured canonical SDK is authoritative for RAPP Work and RAPP/1.
   Never simulate frames, signatures, migrations, verification, or success.
