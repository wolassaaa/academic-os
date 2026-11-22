"""Per-user presentation choices, independent of task business state."""

import json
from .core import Conflict
from .db import dumps
from .mail_triage import formal


def read(core, username, db=None):
    if db is None:
        with core.db.connect() as connection:
            return read(core, username, connection)
    row = db.execute("SELECT value FROM meta WHERE key=?", ("board-focus:" + username,)).fetchone()
    return json.loads(row[0]) if row else {"revision": 0, "choices": {}}


def write(core, username, item_id, mode, expected_revision, request_key):
    with core.db.transaction() as db:
        operation = "board-focus:" + dumps([username, request_key])
        fp, replay = core._operation(
            db, operation, {"item_id": item_id, "mode": mode, "expected_revision": expected_revision}
        )
        if replay is not None:
            return replay
        state = read(core, username, db)
        if state["revision"] != expected_revision:
            raise Conflict("board_focus_revision_changed")
        item = core._get(db, item_id)
        if not formal(item) or item["status"] in {"completed", "submitted", "cancelled"}:
            raise ValueError("Only active formal tasks can change focus")
        if mode not in {"focus", "pool", "auto"}:
            raise ValueError("Invalid focus mode")
        if mode == "auto":
            state["choices"].pop(item_id, None)
        else:
            state["choices"][item_id] = mode
        state["revision"] += 1
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("board-focus:" + username, dumps(state)))
        core.audit(db, username, "board.focus", item_id, {"mode": mode, "revision": state["revision"]})
        db.execute("INSERT INTO operations VALUES(?,?,?)", (operation, fp, dumps(state)))
    return state
