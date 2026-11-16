"""Small transactional review store for the single-host labeling console.

SQLite is the source of truth; JSON remains an export format for the existing
offline relabel tool. Every label decision is append-only for auditability.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import BASE_DIR


class FeedbackStore:
    def __init__(self, path: Path | None = None):
        self.path = path or BASE_DIR / "var" / "feedback.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS label_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    reviewer TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations (
                    image_path TEXT PRIMARY KEY,
                    source_dataset TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL DEFAULT '',
                    model_prediction TEXT NOT NULL DEFAULT '',
                    model_confidence REAL,
                    final_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    evidence_status TEXT NOT NULL DEFAULT 'unclear',
                    action_evidence TEXT NOT NULL DEFAULT 'unclear',
                    reviewer TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    taxonomy_version TEXT NOT NULL DEFAULT 'v1',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relabel_plan (
                    src TEXT PRIMARY KEY,
                    to_label TEXT NOT NULL,
                    from_label TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auto_labels (
                    image_path TEXT PRIMARY KEY,
                    batch TEXT NOT NULL DEFAULT '',
                    prediction TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    model_version TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auto_batch ON auto_labels(batch);
                CREATE TABLE IF NOT EXISTS hard_cases (
                    path TEXT PRIMARY KEY,
                    want TEXT NOT NULL DEFAULT '',
                    got TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL
                );
            """)
            # Forward-compatible migration for feedback.sqlite3 created by older builds.
            columns = {row[1] for row in db.execute("PRAGMA table_info(annotations)")}
            for name, declaration in (
                ("evidence_status", "TEXT NOT NULL DEFAULT 'unclear'"),
                ("action_evidence", "TEXT NOT NULL DEFAULT 'unclear'"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE annotations ADD COLUMN {name} {declaration}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level="IMMEDIATE")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _event(self, db: sqlite3.Connection, path: str, action: str,
               payload: dict[str, Any], reviewer: str = "") -> None:
        db.execute(
            "INSERT INTO label_events(image_path,action,payload,reviewer,created_at) VALUES(?,?,?,?,?)",
            (path, action, json.dumps(payload, ensure_ascii=False), reviewer, self.now()),
        )

    def list_plan(self) -> dict:
        with self.connect() as db:
            items = [dict(row) for row in db.execute(
                "SELECT src,to_label AS 'to',from_label AS 'from',note,reviewer,added_at "
                "FROM relabel_plan ORDER BY added_at,src")]
        return {"items": items}

    def save_plan(self, items: list[dict], reviewer: str = "") -> dict:
        added = changed = 0
        with self.connect() as db:
            for item in items:
                src, target = str(item.get("src") or ""), str(item.get("to") or "")
                if not src or not target:
                    continue
                existing = db.execute("SELECT to_label FROM relabel_plan WHERE src=?", (src,)).fetchone()
                now = self.now()
                if existing and existing["to_label"] == target:
                    continue
                db.execute("""INSERT INTO relabel_plan(src,to_label,from_label,note,reviewer,added_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(src) DO UPDATE SET
                    to_label=excluded.to_label,from_label=excluded.from_label,note=excluded.note,
                    reviewer=excluded.reviewer,added_at=excluded.added_at""",
                    (src, target, str(item.get("from") or ""), str(item.get("note") or ""), reviewer, now))
                self._event(db, src, "relabel_plan.upsert", item, reviewer)
                if existing:
                    changed += 1
                else:
                    added += 1
            total = db.execute("SELECT COUNT(*) FROM relabel_plan").fetchone()[0]
        return {"added": added, "changed": changed, "total": total}

    def remove_plan(self, sources: set[str], reviewer: str = "") -> dict:
        removed = 0
        with self.connect() as db:
            for src in sources:
                row = db.execute("SELECT * FROM relabel_plan WHERE src=?", (src,)).fetchone()
                if not row:
                    continue
                self._event(db, src, "relabel_plan.remove", dict(row), reviewer)
                db.execute("DELETE FROM relabel_plan WHERE src=?", (src,))
                removed += 1
            total = db.execute("SELECT COUNT(*) FROM relabel_plan").fetchone()[0]
        return {"removed": removed, "total": total}

    def record_annotation(self, item: dict, reviewer: str = "") -> dict:
        path = str(item["path"])
        final_label = str(item["label"])
        evidence_status = str(item.get("evidence_status") or "unclear")
        trainable_labels = set(item.get("trainable_labels") or []) if evidence_status == "visible" else set()
        now = self.now()
        payload = {**item, "trainable_labels": sorted(trainable_labels),
                   "reviewer": reviewer, "updated_at": now}
        with self.connect() as db:
            db.execute("""INSERT INTO annotations(image_path,source_dataset,source_label,model_prediction,
                model_confidence,final_label,status,note,evidence_status,action_evidence,reviewer,
                model_version,taxonomy_version,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(image_path) DO UPDATE SET
                source_dataset=excluded.source_dataset,source_label=excluded.source_label,
                model_prediction=excluded.model_prediction,model_confidence=excluded.model_confidence,
                final_label=excluded.final_label,status=excluded.status,note=excluded.note,
                evidence_status=excluded.evidence_status,action_evidence=excluded.action_evidence,
                reviewer=excluded.reviewer,model_version=excluded.model_version,
                taxonomy_version=excluded.taxonomy_version,updated_at=excluded.updated_at""",
                (path, str(item.get("source_dataset") or ""), str(item.get("source_label") or ""),
                 str(item.get("prediction") or ""), item.get("confidence"), final_label,
                 str(item.get("status") or "reviewed"), str(item.get("note") or ""),
                 str(item.get("evidence_status") or "unclear"),
                 str(item.get("action_evidence") or "unclear"), reviewer,
                 str(item.get("model_version") or ""), str(item.get("taxonomy_version") or "v1"), now))
            self._event(db, path, "annotation.upsert", payload, reviewer)
            source_label = str(item.get("source_label") or "")
            if final_label in trainable_labels and final_label != source_label:
                plan = {"src": path, "from": source_label, "to": final_label,
                        "note": str(item.get("note") or "Reviewed label"), "reviewer": reviewer}
                db.execute("""INSERT INTO relabel_plan(src,to_label,from_label,note,reviewer,added_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(src) DO UPDATE SET
                    to_label=excluded.to_label,from_label=excluded.from_label,note=excluded.note,
                    reviewer=excluded.reviewer,added_at=excluded.added_at""",
                    (path, final_label, source_label, plan["note"], reviewer, now))
                self._event(db, path, "relabel_plan.upsert", plan, reviewer)
            else:
                # A corrected decision to keep the source label, or to mark it
                # other/uncertain, must remove any stale training move.
                db.execute("DELETE FROM relabel_plan WHERE src=?", (path,))
        return {"path": path, "label": final_label, "updated_at": now,
                "training_ready": final_label in trainable_labels}

    def list_annotations(self, limit: int = 500) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM annotations ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def undo_annotation(self, path: str, reviewer: str = "") -> dict:
        """Restore the previous reviewed state (or remove the first decision)."""
        with self.connect() as db:
            events = db.execute("""SELECT id,action,payload FROM label_events
                WHERE image_path=? AND action IN ('annotation.upsert','annotation.undo') ORDER BY id""",
                (path,)).fetchall()
            undone = {json.loads(row["payload"]).get("undo_target_id") for row in events
                      if row["action"] == "annotation.undo"}
            active = [row for row in events if row["action"] == "annotation.upsert"
                      and row["id"] not in undone]
            if not active:
                return {"undone": False, "path": path}
            current_row = active[-1]
            previous_row = active[-2] if len(active) > 1 else None
            previous = json.loads(previous_row["payload"]) if previous_row else None
            if previous:
                previous["reviewer"] = reviewer
                # Write latest state inline to keep this transaction atomic.
                now = self.now()
                label = str(previous.get("label") or "uncertain")
                db.execute("""UPDATE annotations SET source_dataset=?,source_label=?,model_prediction=?,
                    model_confidence=?,final_label=?,status=?,note=?,evidence_status=?,action_evidence=?,
                    reviewer=?,model_version=?,
                    taxonomy_version=?,updated_at=? WHERE image_path=?""",
                    (str(previous.get("source_dataset") or ""), str(previous.get("source_label") or ""),
                     str(previous.get("prediction") or ""), previous.get("confidence"), label,
                     str(previous.get("status") or "reviewed"), str(previous.get("note") or ""),
                     str(previous.get("evidence_status") or "unclear"),
                     str(previous.get("action_evidence") or "unclear"), reviewer,
                     str(previous.get("model_version") or ""), str(previous.get("taxonomy_version") or "v1"),
                     now, path))
                if label in set(previous.get("trainable_labels") or []) and label != str(previous.get("source_label") or ""):
                    db.execute("""INSERT INTO relabel_plan(src,to_label,from_label,note,reviewer,added_at)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(src) DO UPDATE SET to_label=excluded.to_label,
                        from_label=excluded.from_label,note=excluded.note,reviewer=excluded.reviewer,
                        added_at=excluded.added_at""",
                        (path, label, str(previous.get("source_label") or ""),
                         str(previous.get("note") or "Reviewed label"), reviewer, now))
                else:
                    db.execute("DELETE FROM relabel_plan WHERE src=?", (path,))
            else:
                db.execute("DELETE FROM annotations WHERE image_path=?", (path,))
                db.execute("DELETE FROM relabel_plan WHERE src=?", (path,))
            self._event(db, path, "annotation.undo",
                        {"undo_target_id": current_row["id"],
                         "restored_event_id": previous_row["id"] if previous_row else None},
                        reviewer)
        return {"undone": True, "path": path}

    def latest_paths(self) -> set[str]:
        with self.connect() as db:
            return {row[0] for row in db.execute("SELECT image_path FROM annotations")}

    def save_hard_cases(self, items: list[dict], reviewer: str = "") -> dict:
        added = 0
        with self.connect() as db:
            for item in items:
                path = str(item.get("path") or "")
                if not path:
                    continue
                cursor = db.execute("""INSERT OR IGNORE INTO hard_cases
                    (path,want,got,note,reviewer,added_at) VALUES(?,?,?,?,?,?)""",
                    (path, str(item.get("want") or ""), str(item.get("got") or ""),
                     str(item.get("note") or ""), reviewer, self.now()))
                if cursor.rowcount:
                    added += 1
                    self._event(db, path, "hard_case.add", item, reviewer)
            total = db.execute("SELECT COUNT(*) FROM hard_cases").fetchone()[0]
        return {"added": added, "total": total}

    def list_hard_cases(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM hard_cases ORDER BY added_at")]

    def save_auto_labels(self, batch: str, items: list[dict], model_version: str = "") -> int:
        """记录模型预标注结果。不覆盖人工结论，只做排队依据。"""
        n = 0
        now = self.now()
        with self.connect() as db:
            for it in items:
                path = str(it.get("path") or "")
                if not path:
                    continue
                db.execute("""INSERT INTO auto_labels(image_path,batch,prediction,confidence,model_version,created_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(image_path) DO UPDATE SET
                    batch=excluded.batch, prediction=excluded.prediction,
                    confidence=excluded.confidence, model_version=excluded.model_version,
                    created_at=excluded.created_at""",
                    (path, batch, str(it.get("prediction") or ""), it.get("confidence"),
                     model_version, now))
                n += 1
        return n

    def list_auto_labels(self, batch: str = "", limit: int = 2000) -> list[dict]:
        sql = ("SELECT image_path AS path, batch, prediction, confidence, model_version, created_at "
               "FROM auto_labels")
        args: tuple = ()
        if batch:
            sql += " WHERE batch = ?"
            args = (batch,)
        sql += " ORDER BY confidence ASC LIMIT ?"
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, (*args, max(1, min(limit, 5000))))]

    def auto_label_stats(self, batch: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) n, AVG(confidence) avg_conf, "
                "SUM(CASE WHEN confidence >= 0.85 THEN 1 ELSE 0 END) hi "
                "FROM auto_labels WHERE batch = ?", (batch,)).fetchone()
        return {"labeled": row["n"] or 0,
                "avg_confidence": round(row["avg_conf"], 4) if row["avg_conf"] else None,
                "high_confidence": row["hi"] or 0}

    def import_legacy_plan(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload.get("items"), list):
                self.save_plan(payload["items"], reviewer="legacy-import")
        except (OSError, ValueError, TypeError):
            return


feedback_store = FeedbackStore()
