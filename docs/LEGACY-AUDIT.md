# Legacy audit and migration boundaries

Audit date: 2026-10-12. The authoritative implementation inspected for reuse was
`D:\AgentNode\Core\Hermes\academic_os_src\academic_os`. The separately deployed
`academic_os` directory contains older scripts and private data; its code is not an
import dependency of v2. Inspection read Python source and data schemas locally.
No legacy Python module was imported, no scheduled job was stopped, and no remote
write or message was performed by this audit.

## Read code retained

`client.py`, `models.py`, and `deadlines.py` supply the Moodle REST whitelist,
deadline structures, and dedicated assignment/quiz/calendar normalization.
Audited copies live in `src/academic_os/vendor`. Their hashes and adaptations are
recorded in `vendor/provenance.json` and [REUSE.md](REUSE.md).

The legacy `moodle.py`, `full_activities.py`, `submissions.py`,
`submission_index.py`, `baseline_assessments.py`, and `hermes.py` were inspected
for data semantics. Their orchestration was replaced because:

- Full-activity scans and baseline reads silently failed while `complete=True`.
- Unknown due dates were represented as 2099 timestamps.
- Date-only baseline entries were assigned an invented 23:59 deadline.
- Assignment status treated files attached to a draft as submitted.
- A course-module ID and a submission API instance ID were sometimes conflated.
- A submitted assessment could be lost from action events; the dedicated lists
  and complete course contents must also be read.

V2 reads Moodle directly with proxies disabled, a 30-second per-call timeout,
an overall collection budget, and a finite pagination limit. REST parameters
cannot override the whitelist or token. Each failed read makes the snapshot
incomplete; available records remain usable. Failed submission reads produce
`unknown` and sanitized failure evidence. Unsupported module submission APIs
also produce `unknown`, without inventing submission evidence. Raw successful
submission payloads are preserved in each record's JSON `evidence_text` after
credential fields and unsafe URL query fields are removed. Numeric `id`, `courseid`,
and `cmid` URL fields are retained so Moodle source links remain usable. The core must retain this evidence
and ingest partial records before recording source failure.

Unknown dates are `due_at=None`/`needs_date`. When a dedicated deadline API fails,
a partial course-content record omits `due_at` entirely so the core can retain a
previously observed date. Baseline date-only values remain in evidence and the
copied baseline artifact. They do not become midnight, end-of-day, or execution
slots.

Explicit module `dates` entries labeled Due/Due date/Closes (or recognized literal
Chinese equivalents) may provide an exact Unix timestamp for activities such as
Turnitin or Lesson. Open dates, generic dates, conflicting due instants, date-only
values, and unrecognized labels never become a deadline. Dedicated assignment/quiz
deadline APIs take precedence. Original module date entries remain in evidence.

## Excluded executable behavior

The legacy sync/store/notification/Google/browser/login/CLI modules are not
vendored or executed. They can write calendar events, notification state,
credentials, caches, or reminders. The old `__init__.py` is not reused even though
the inspected source package initializer was currently inert. V2 never extends
`sys.path` to the old tree and requires no original source path at runtime.

## Imported data

`import_legacy(core, root, import_credentials=False)` uses a fixed file allowlist,
not a recursive workspace copy. It reads safe assessment, grade, activity, reminder,
calendar-map, and mail-date snapshots into `data/imports`, with original and copied
SHA-256 evidence. Secret fields and unsafe URL query fields are excluded from copied JSON.
Source files are never rewritten. Symlinks and destination paths inside the old
root are rejected.

The timetable is extracted through `ast.parse`/`ast.literal_eval`, never imported.
It is retained as an artifact with `needs_term_review=True`; its missing term dates
do not silently turn an old timetable into current availability.

Historical reminder and escalation state remains an artifact; it does not suppress
v2 reminders or impersonate a v2 delivery receipt. Mail-date state remains historical
source evidence, not a new mail fetch or a calendar-write request. No assessment
items or execution slots are created solely from these historical files.

The root `gcal_feishu_state.json` is copied as `legacy_feishu_mappings`, together
with the literal `FEISHU_CAL_ID` and source-file hash extracted from
`scripts/gcal_to_feishu.py`. `adopt_legacy_feishu_mappings(core)` requires that
verified target to equal the configured Feishu calendar. It then matches only
configured Google calendars and exact cached Google event identities. Recurring
legacy instance IDs are bridged to bounded `mirror_instances` occurrences by their
original UTC timestamp; cached cancelled exceptions retain deletion intent when
their master is known. No title/date similarity is used. Unmatched or ambiguous
entries remain artifacts and existing mappings are never overwritten. Adopted rows
retain the old Feishu event ID with a missing content hash so the next mirror patches
that existing event instead of creating a duplicate. The helper performs local
mapping writes only and never reads the original source tree at runtime.

Calendar event mappings are restored only when an exact source ID matches exactly
one internal item and provider/calendar/event identity is known. Bare event-ID maps
also require a literal legacy calendar ID to match a configured Google account's
calendar metadata. Ambiguous entries remain in the map artifact. Existing mappings
are never overwritten. Restored mappings have `purpose=legacy_deadline`; calendar
sync must not interpret their times as user execution slots.

## Credential migration and restoration

Only explicit `import_credentials=True` reads selected legacy keyring entries,
`google_token*.json`, or `.env`. Moodle and selected Google refresh credentials go
directly into the `academic-os-v2` OS keyring. The two documented Feishu application
keys may also be transferred; `.env` is parsed as text. No environment or credential
file is executed or copied, and credentials never appear in results/manifests.
Account/calendar write permissions and notification recipients require normal v2
configuration. Calling code must save the changed settings after import.

`backup_data(core, destination)` writes a SQLite online backup, copied import files,
hashes, and safe settings metadata to an empty directory outside the data directory.
OS keyring secrets are excluded. `restore_data(core, manifest_or_directory)` validates
paths, hashes, SQLite integrity, and schema before mutation; it rejects an active
worker lock. It restores local database/imports, invalidates API tokens and sessions,
clears leases/heartbeat, restores validated settings/account/calendar identities,
forces shadow mode with loopback binding and model analysis disabled, and rebases
copied source paths. Missing OS keyring handles are reported without credential
contents. Caller must persist settings. No ZIP extraction is supported. These helpers do not control
old processes or jobs and cannot send, publish, or synchronize remote state.
