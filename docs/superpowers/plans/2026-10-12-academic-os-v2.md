# Academic OS 2.0 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution; user explicitly requested implementation of the approved plan.

**Goal:** Deliver a portable Windows academic hub, remote read-only dashboard, Agent APIs and calendar/notification pipelines.

**Architecture:** Python modular core with SQLite, FastAPI management and read-only applications, durable worker, plain HTML/CSS/JS responsive frontend. Adapters isolate credentials and network policy. Shadow mode is the initial default.

**Tech Stack:** Python 3.11+, SQLite, FastAPI, httpx, uvicorn, keyring, pywin32, pytest.

**Spec:** ../../Academic-OS-2.0-规格.md

## Global Constraints
Model analysis disabled. Single active writer. Moodle read-only. Google/Apple editable; Feishu mirror-only. Public dashboard read-only. No credentials in exports. Existing Hermes jobs stay active during shadow operation.

## Review Focus
Concurrent requests and restart recovery; stale source data; timezone/all-day boundaries; externally changed or deleted calendar events; remote privilege escalation and secret exposure.

### Task 1: Durable core, security and configuration
Files: src/academic_os/{config,db,security,core}.py; tests/test_core.py.
Interfaces: Settings.load(path), Database(path), Core(db,settings); stable IDs, optimistic versions, idempotent operations and audit log.
- [x] Write and observe failing tests for upsert/dedup, version conflict, draft confirmation, auth and backup.
- [x] Implement transactions, schema, credential handles and runtime configuration.
- [x] Run core suite and record evidence.

### Task 2: Source adapters, rule extraction, planning and exports
Files: sources/, extraction.py, planner.py, exports.py, migration.py; tests/test_sources_planning.py.
Interfaces: normalized source records -> Core.ingest; Planner.generate -> draft; Exporter.write -> latest.md.
- [x] Test multiple dates, source conflict, submitted state, occupied slots, timezone and unplaced work.
- [x] Implement read-only Moodle process, Gmail clients, local grade/timetable/archive feeds, migration manifest.
- [x] Verify deterministic offline fixture and real local snapshot import.

### Task 3: Calendar and notification workers
Files: calendars.py, notifications.py, worker.py, providers.py; tests/test_sync_delivery.py.
Interfaces: persist jobs, lease claims, provider transports, calendar mappings and cursors.
- [x] Test remote change/delete/conflict, paging and invalid cursor; failed deliveries and retries.
- [x] Implement Google incremental pull/push, Feishu mirror, notification outbox, health and recovery.
- [x] Validate shadow mode prevents external effects.

### Task 4: HTTP, MCP and workbench
Files: api.py, mcp.py, web/*; tests/test_api.py, tests/test_mcp.py.
Interfaces: /api/v1 resources, scoped Agent tools, authenticated remote read-only app.
- [x] Test login, expiry, rate limits, scopes, denied remote mutations, idempotent drafts and confirmation.
- [x] Implement workbench, remote status view, contract specs and browser verification.
- [x] Verify real local HTTP and screenshots.

### Task 5: Windows service, portable package and operational docs
Files: cli.py, windows_service.py, scripts/*, docs/*.
- [x] Test CLI initialization, backup/restore, package exclusion and restart recovery.
- [x] Implement service/bootstrap, safe secret import, read-only legacy snapshot migration and cutover/rollback.
- [x] Full suite, lint, package build, fresh-context review, fix findings, final verification.
