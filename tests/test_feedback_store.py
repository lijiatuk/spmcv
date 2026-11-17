import sqlite3

from app.services.feedback_store import FeedbackStore


def test_relabel_plan_updates_and_removes_transactionally(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite3")
    first = store.save_plan([{"src": "/dataset/a.jpg", "from": "x", "to": "bridge_drain"}])
    assert first == {"added": 1, "changed": 0, "total": 1}

    changed = store.save_plan([{"src": "/dataset/a.jpg", "from": "x", "to": "filter_has"}])
    assert changed == {"added": 0, "changed": 1, "total": 1}
    assert store.list_plan()["items"][0]["to"] == "filter_has"

    assert store.remove_plan({"/dataset/a.jpg"}) == {"removed": 1, "total": 0}


def test_other_and_uncertain_never_become_training_labels(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite3")
    base = {
        "path": "/dataset/a.jpg", "source_label": "bridge_drain",
        "prediction": "filter_has", "confidence": 0.93,
        "trainable_labels": ["bridge_drain", "filter_has"],
    }
    store.record_annotation({**base, "label": "other", "status": "other"})
    assert store.list_plan()["items"] == []
    assert store.list_annotations()[0]["status"] == "other"

    store.record_annotation({**base, "label": "filter_has", "status": "reviewed",
                             "evidence_status": "visible", "action_evidence": "drain",
                             "note": "filter body and oil stream visible"})
    assert store.list_plan()["items"][0]["to"] == "filter_has"
    store.record_annotation({**base, "label": "uncertain", "status": "uncertain"})
    assert store.list_plan()["items"] == []


def test_unclear_or_partial_evidence_never_enters_training_plan(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite3")
    item = {
        "path": "/dataset/a.jpg", "source_label": "bridge_drain",
        "label": "filter_has", "evidence_status": "partial",
        "trainable_labels": ["bridge_drain", "filter_has"],
    }
    result = store.record_annotation(item)
    assert result["training_ready"] is False
    assert store.list_plan()["items"] == []

    result = store.record_annotation({**item, "evidence_status": "visible"})
    assert result["training_ready"] is True
    assert store.list_plan()["items"][0]["to"] == "filter_has"


def test_annotation_undo_restores_prior_decision_and_training_plan(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite3")
    base = {
        "path": "/dataset/a.jpg", "source_label": "bridge_drain",
        "prediction": "filter_has", "confidence": 0.93,
        "trainable_labels": ["bridge_drain", "filter_has"],
    }
    store.record_annotation({**base, "label": "filter_has", "status": "reviewed",
                             "evidence_status": "visible", "action_evidence": "drain",
                             "note": "filter body and oil stream visible"})
    store.record_annotation({**base, "label": "uncertain", "status": "uncertain"})

    assert store.undo_annotation("/dataset/a.jpg")["undone"] is True
    assert store.list_annotations()[0]["final_label"] == "filter_has"
    assert store.list_annotations()[0]["evidence_status"] == "visible"
    assert store.list_annotations()[0]["action_evidence"] == "drain"
    assert store.list_annotations()[0]["note"] == "filter body and oil stream visible"
    assert store.list_plan()["items"][0]["to"] == "filter_has"
    assert store.undo_annotation("/dataset/a.jpg")["undone"] is True
    assert store.list_annotations() == []
    assert store.list_plan()["items"] == []


def test_store_migrates_existing_annotation_table(tmp_path):
    path = tmp_path / "feedback.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE annotations (
            image_path TEXT PRIMARY KEY, source_dataset TEXT NOT NULL DEFAULT '',
            source_label TEXT NOT NULL DEFAULT '', model_prediction TEXT NOT NULL DEFAULT '',
            model_confidence REAL, final_label TEXT NOT NULL, status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL DEFAULT '',
            model_version TEXT NOT NULL DEFAULT '', taxonomy_version TEXT NOT NULL DEFAULT 'v1',
            updated_at TEXT NOT NULL)""")
    store = FeedbackStore(path)
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(annotations)")}
    assert {"evidence_status", "action_evidence"}.issubset(columns)
    store.record_annotation({"path": "/dataset/a.jpg", "label": "uncertain"})
    row = store.list_annotations()[0]
    assert row["evidence_status"] == "unclear"
    assert row["action_evidence"] == "unclear"
