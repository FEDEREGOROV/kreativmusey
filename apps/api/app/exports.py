from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ExportJob, Message, utcnow


settings = get_settings()


def _safe_csv(value: str | None) -> str:
    text = value or ""
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def create_export(
    db: Session,
    admin_id: str,
    file_format: str,
    mode: str,
    limit: int | None,
) -> ExportJob:
    watermark = db.scalar(select(func.max(Message.sequence))) or 0
    job = ExportJob(
        created_by=admin_id,
        format=file_format,
        state="processing",
        filters_json=json.dumps({"mode": mode}, ensure_ascii=False),
        requested_count=limit if mode == "last" else None,
        snapshot_watermark=watermark,
        expires_at=utcnow() + timedelta(hours=settings.export_ttl_hours),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    query = select(Message).where(Message.sequence <= watermark).order_by(Message.sequence.desc())
    if mode == "last" and limit:
        query = query.limit(limit)
    messages = list(reversed(db.scalars(query).all()))
    settings.export_path.mkdir(parents=True, exist_ok=True)
    path = settings.export_path / f"{job.id}.{file_format}"
    fields = [
        "message_id", "conversation_id", "role", "text", "created_at",
        "result_status", "request_id", "channel", "source_ids", "kb_version",
    ]
    rows = [
        {
            "message_id": message.id,
            "conversation_id": message.conversation_id,
            "role": message.role,
            "text": message.text,
            "created_at": message.created_at.isoformat(),
            "result_status": message.result_status,
            "request_id": message.request_id,
            "channel": "web",
            "source_ids": json.loads(message.source_ids_json or "[]"),
            "kb_version": message.kb_version_id,
        }
        for message in messages
    ]
    if file_format == "json":
        path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "created_at": job.created_at.isoformat(),
                        "snapshot_watermark": watermark,
                        "timezone": "UTC",
                        "sort": "oldest-first",
                        "actual_count": len(rows),
                    },
                    "messages": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                csv_row = {field: row[field] for field in fields}
                csv_row["text"] = _safe_csv(str(csv_row["text"]))
                csv_row["source_ids"] = json.dumps(csv_row["source_ids"], ensure_ascii=False)
                writer.writerow(csv_row)

    job.state = "ready"
    job.actual_count = len(rows)
    job.file_key = str(path)
    db.commit()
    db.refresh(job)
    return job


def export_path(job: ExportJob) -> Path | None:
    if job.state != "ready" or job.expires_at <= utcnow() or not job.file_key:
        return None
    path = Path(job.file_key)
    return path if path.exists() else None
