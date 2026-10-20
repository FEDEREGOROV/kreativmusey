from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func, select

from .db import SessionLocal, init_db
from .knowledge import import_knowledge_file, publish_version
from .models import KnowledgeChunk, KnowledgeVersion


def main() -> None:
    parser = argparse.ArgumentParser(prog="creative-museum")
    commands = parser.add_subparsers(dest="command", required=True)
    kb = commands.add_parser("kb")
    kb_commands = kb.add_subparsers(dest="kb_command", required=True)

    import_parser = kb_commands.add_parser("import")
    import_parser.add_argument("--source", type=Path, required=True)
    import_parser.add_argument("--season", required=True)
    import_parser.add_argument("--publish", action="store_true")

    validate_parser = kb_commands.add_parser("validate")
    validate_parser.add_argument("--version", required=True)

    publish_parser = kb_commands.add_parser("publish")
    publish_parser.add_argument("--version", required=True)

    rollback_parser = kb_commands.add_parser("rollback")
    rollback_parser.add_argument("--to", required=True)

    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        if args.kb_command == "import":
            version, count = import_knowledge_file(
                db,
                args.source.resolve(),
                args.season,
                "draft",
            )
            if args.publish:
                version = publish_version(db, version.id)
            print(json.dumps({"version_id": version.id, "status": version.status, "chunks": count}, ensure_ascii=False))
        elif args.kb_command == "validate":
            version = db.get(KnowledgeVersion, args.version)
            if not version:
                raise SystemExit("Версия базы не найдена")
            count = db.scalar(select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.version_id == version.id)) or 0
            print(json.dumps({"version_id": version.id, "status": version.status, "chunks": count, "valid": count > 0}, ensure_ascii=False))
        elif args.kb_command in {"publish", "rollback"}:
            version_id = args.version if args.kb_command == "publish" else args.to
            version = publish_version(db, version_id)
            print(json.dumps({"version_id": version.id, "status": version.status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
