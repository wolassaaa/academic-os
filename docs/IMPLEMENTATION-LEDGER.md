# Implementation ledger — plan: docs/superpowers/plans/2026-10-12-academic-os-v2.md

2026-10-12: user approved implementation; then explicitly requested model interface only, no analysis enabled.
Ruling: work in the newly created empty repository on codex/academic-os-2; no existing code or branch changes to isolate.
Ruling: local Windows deployment; Sites publication is inapplicable because the approved host is Windows with Funnel.
Ruling: do not re-request review of the already approved plan; execute inline as explicitly requested.
Ruling: use a buildless frontend served from the Python package to keep installation portable.
Pre-flight: Core is the authority used by workers, HTTP and MCP; snapshot exports are read-only. Provider adapters never own user-facing state.

Task 1: implemented — SQLite/WAL authority, optimistic versions, transactional draft confirmation, scoped Agent tokens, independent sessions and audited operations.
Task 2: implemented — Gmail, read-only Moodle subprocess, local snapshots/baselines, deterministic rule extraction/planning and atomic latest.md. Model contract exists; analysis is disabled and enabling it is rejected.
Task 3: implemented — bounded/incremental Google sync, conditional writes, deletion intent, Feishu mirror, persisted notification outbox, retries and independent notifier/heartbeat supervision.
Task 4: implemented — versioned HTTP API, official MCP SDK adapter, buildless workbench and separate authenticated read-only dashboard; real Chrome desktop/mobile verification.
Task 5: implementation complete — native Windows service/bootstrap, portable source package, backup/restore, explicit cutover/rollback and deployment documentation. Live deployment acceptance remains pending as listed below.

## 2026-10-12 verification and review

- Independent review corrected source-date conflict handling, stale reminders during concurrent edits, permission boundaries, worker partial failures and DST recurrence lookback. Regression tests include each corrected path.
- Full automated suite and lint are run on the final deliverable; detailed commands and acceptance boundaries are in [ACCEPTANCE.md](ACCEPTANCE.md).
- Existing Hermes data imported read-only into ignored local shadow state: 16 snapshots and 35 assessment-baseline items, zero imported credentials and zero guessed calendar mappings. Legacy jobs are unchanged.
- Source package excludes real config, database, Credential Manager secrets, build artifacts and virtual environments. Wheel installation is checked in a fresh virtual environment.
- No model provider requests, live notification delivery, Google/Feishu writes, SCM registration or Funnel publication were performed.

## Pending deployment acceptance

Actual Google/Gmail/Moodle/Feishu credentials and permissions; Google/Apple real-device edits; delivery receipts; native Windows service boot/recovery under the target user; public HTTPS/mobile login; and takeover on a second physical Windows computer. Offline fixtures and a fresh local virtual environment do not establish these outcomes. Keep shadow mode until the cutover checklist has been completed.

2026-10-12 portable follow-up: bundled Windows x64 runtime; no Python installation or dependency download on target. Graphical launcher, password recovery and safe stop share existing Core. 106 tests pass; relocated frozen EXE diagnostics and MCP handshake pass. Build refuses user-state overwrite. Previous temporary source preview stopped for trial port availability; its state remains intact.

2026-10-12 account-management follow-up: local owner-only Google desktop OAuth with PKCE, session-bound single-use callback, real Gmail/calendar discovery, calendar selection, reconnect/disconnect; Moodle official mobile browser SSO callback and verified token fallback; fixed-directory legacy credential adoption. Model stays disabled. Worker uses current connections per job. Main UI is maintenance/settings with three main entries. Credentials never enter packages/config/exports. Real local legacy credentials were validated against two Gmail profiles and CalendarList, and HKBU Moodle site info + 21 courses. No live calendar/Feishu write or notification was sent; interactive school browser SSO and second-device migration still need human/environment acceptance. Review fixes cover gmail.send scope, revoked sessions/in-flight disconnect, disconnected calendar caches and duplicate events when changing write target.

Final follow-up verification: 117 tests and Ruff pass; Chrome full workflow passes. Recheck fixed disconnect during Feishu mirror loop by verifying current active source under the configuration lock for each external mutation. Real Moodle complete collector read 55 records, 39 known deadlines and 22 submitted activities. Snapshot remains explicitly incomplete because some school modules/quiz attempts are inaccessible or unknown; warnings now retain accessible assignment payloads and submission status instead of discarding the whole feed. These are displayed as partial failures, not reported as full success.

User clean-login trial: diagnosed actual startup collision with our hidden frozen preview PID holding both 8765 and 8766. Verified its executable and preview arguments, stopped only that process, and left no preview running. Cleared Google/Moodle config, owner sessions and agent tokens in the project preview and two extracted trial installations under InstanceLock; superseded pending source/calendar jobs and removed account credentials only from academic-os-v2 Credential Manager. Google desktop OAuth application configuration is retained; legacy academic-os credentials and original files are unchanged. GUI now reports the affected socket port and actionable conflict text, with safe numeric diagnostics in desktop.log. User must start and log in themselves; no auto-import or automatic preview startup follows this reset.
