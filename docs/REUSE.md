# Reuse provenance

V2 retains audited read-only parts of the user's internal legacy Academic OS.
This is internal code reuse, **not a claim that the legacy code is third-party
open source**. No explicit license grant was found in the audited module set;
ownership/license remains internal/unknown. Do not assign an SPDX open-source
license to it without establishing the relevant rights.

The source package used was
`D:\AgentNode\Core\Hermes\academic_os_src\academic_os`, inspected 2026-10-12.
That location is audit provenance only. V2 runs from its installed package and
does not import code or data from the original tree.

| V2 module | Original module | Retained behavior | Adaptation |
|---|---|---|---|
| `vendor/moodle_client.py` | `client.py` | Moodle REST protocol, read whitelist, sanitized API error structure | Direct transport regardless of proxy environment; reject URL credentials/query/fragment; prevent reserved parameter override |
| `vendor/models.py` | `models.py` | Immutable deadline data structures | Formatting only |
| `vendor/deadlines.py` | `deadlines.py` | Calendar, assignment and quiz deadline normalization | Reject invalid/overflow/far-future sentinel timestamps; modern import formatting |

`vendor/provenance.json` records SHA-256 hashes for the original audited sources
and retained files. Original-file hashes identify the exact source snapshot;
retained-file hashes identify the adapted copies. The new `moodle_process.py`
adapter applies the reviewed legacy data semantics without importing legacy
orchestration, notifications, reminder writers, browser authentication, or Google
calendar writers. Its module-type constants and baseline source-ID scheme derive
from inspected `full_activities.py`/`baseline_assessments.py`; submission normalization
uses explicit raw Moodle status values and preserves source evidence.

Python, FastAPI, httpx, keyring, tzdata, python-dateutil, and other declared package
dependencies are separate third-party dependencies installed normally. Their
licenses remain those of their respective packages. No third-party source code
was copied into `vendor` as part of this reuse.

See [LEGACY-AUDIT.md](LEGACY-AUDIT.md) for excluded behaviors and migration boundaries.
