# One staff-to-visitor demonstration

User scope: a single independent, demonstrable case; no production staff account system or conservation RAG in this increment.

## Flow

Open `/staff-demo`. On the locally held official floor map, show one synthetic crowd snapshot and one synthetic humidity reading. The operator opens the preset public guidance summary, acknowledges its limitations, then confirms a simulated gallery restriction. The visitor preview updates using the existing bounded route planner with that gallery excluded from both destinations and transit. Reset or refresh restores the initial case.

The case's humidity reminder threshold is an invented demonstration parameter, not a museum policy or a value sourced from the cited guideline. Crowding is not presented as a proven cause of humidity. After confirmation the snapshot is retained; the page makes no claim that crowds dispersed or environmental conditions improved.

## Isolation and permissions

This is a public role-workflow demonstration. `/api/museum/demo/operations` returns only the explicitly configured synthetic case and public-source summaries; it is read-only. It does not read conversation traces or internal conservation records, assign staff roles, change the live route planner, or publish restrictions. The affected gallery is closed only in a request-local planner copy. If all paths are disconnected, return unavailable; never invent a detour.

Existing `/api/museum/admin/traces` remains behind its server-side admin credential check. A client-provided role label must not grant access. Future conservation integration should establish identity at login, then authorize each retrieval, tool call and publication server-side. Public and internal corpora, retrieved context and conversation memory must remain access-scoped. Decisions may require a separate publisher role from read-only staff. This design is a future requirement, not a shipped account system.

## Configuration and rollback

`MUSEUM_OPERATIONS_DEMO_MANIFEST` points to a local `DemoCase` JSON; fields are defined in `backend/app/museum/operations_demo.py`. It includes the affected gallery, fixed preferences, synthetic crowd points/environment readings and an attributed public guidance summary. `MUSEUM_FLOOR_DEMO_MANIFEST` supplies the unchanged official map images. Both manifests and real-map coordinates remain in ignored local storage.

The guidance source is [CCI Climate guidelines overview](https://www.canada.ca/en/conservation-institute/services/preventive-conservation/climate-guidelines/climate-guidelines-overview.html). The page contains a short preset paraphrase with source access, not a live retrieval or document-verification claim.

Remove the operations-demo setting to disable the scenario. Main visitor conversations and visit planning are unaffected. No paid model calls, new database, location permission or personal tracking is used by this scenario.

## Checks

Synthetic tests cover exclusion of a restricted gallery as both a stop and transit, unchanged live planner state across repeated previews, no invented path when disconnected, existing admin protection, rejection of write requests to the demo endpoint, and the unconfigured state. Browser evidence is recorded locally after the interaction check.

2026-09-30: 151 Python checks and 4 frontend request checks passed, with one existing Starlette/httpx deprecation warning. Production build and type checking passed. Browser checks verified the disabled action before review, enabling after acknowledgement, visitor preview changes after confirmation, and reset restoring the original preview and gate. At 390px there was no horizontal overflow; at 1100px the map and staff review used two columns. This is development verification, not a real staff trial or positioning test.
