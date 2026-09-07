"""Extend the existing participant erasure paths to historical source copies."""
import json

from app.extensions import db
from app.models import HistoricalImportBatch, HistoricalImportSource


def redact_sources(participant_id, *, delete_links=False):
    rows = HistoricalImportSource.query.filter_by(participant_id=participant_id).all()
    keys_by_batch = {}
    for row in rows:
        if row.kind == "participant":
            keys_by_batch.setdefault(row.batch_id, set()).add(row.source_key)
        row.raw_json = json.dumps({"redacted": True})
        row.decision_json = json.dumps({"redacted": True,
            "target_linked": json.loads(row.decision_json).get("target_linked", row.created_target)})
        if delete_links:
            row.participant_id = None
            row.presence_id = None
    for batch_id, keys in keys_by_batch.items():
        batch = db.session.get(HistoricalImportBatch, batch_id)
        decisions = json.loads(batch.decisions_json)
        for key in keys:
            # No name corrections or manual group labels survive anonymisation.
            decisions.get("participants", {}).pop(key, None)
        batch.decisions_json = json.dumps(decisions, ensure_ascii=False)
