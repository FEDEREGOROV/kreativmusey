from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Admin, AdminSession, VisitorSession, utcnow


password_hash = PasswordHash.recommended()
settings = get_settings()


def token_digest(value: str) -> str:
    return hashlib.sha256(f"{settings.session_secret}:{value}".encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def get_or_create_visitor(db: Session, raw_token: str | None) -> tuple[VisitorSession, str | None]:
    if raw_token:
        visitor = db.scalar(
            select(VisitorSession).where(
                VisitorSession.token_hash == token_digest(raw_token),
                VisitorSession.expires_at > utcnow(),
            )
        )
        if visitor:
            return visitor, None

    created_token = new_token()
    visitor = VisitorSession(
        token_hash=token_digest(created_token),
        expires_at=utcnow() + timedelta(days=365),
    )
    db.add(visitor)
    db.commit()
    db.refresh(visitor)
    return visitor, created_token


def seed_admin(db: Session) -> None:
    existing = db.scalar(select(Admin).where(Admin.username == settings.admin_username))
    if existing:
        return
    if not settings.demo_mode and settings.admin_password == "konkursadmin":
        raise RuntimeError("Небезопасный demo-пароль запрещён вне DEMO_MODE")
    db.add(Admin(username=settings.admin_username, password_hash=password_hash.hash(settings.admin_password)))
    db.commit()


def authenticate_admin(db: Session, username: str, password: str) -> Admin | None:
    admin = db.scalar(select(Admin).where(Admin.username == username))
    if not admin or not password_hash.verify(password, admin.password_hash):
        return None
    return admin


def create_admin_session(db: Session, admin: Admin) -> tuple[AdminSession, str, str]:
    raw_token = new_token()
    csrf_token = new_token()
    session = AdminSession(
        admin_id=admin.id,
        token_hash=token_digest(raw_token),
        csrf_hash=token_digest(csrf_token),
        expires_at=utcnow() + timedelta(hours=12),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, raw_token, csrf_token


def resolve_admin_session(db: Session, raw_token: str | None) -> tuple[AdminSession, Admin] | None:
    if not raw_token:
        return None
    session = db.scalar(
        select(AdminSession).where(
            AdminSession.token_hash == token_digest(raw_token),
            AdminSession.expires_at > utcnow(),
        )
    )
    if not session:
        return None
    admin = db.get(Admin, session.admin_id)
    if not admin:
        return None
    session.last_seen_at = utcnow()
    db.commit()
    return session, admin


def verify_csrf(session: AdminSession, raw_csrf: str | None) -> bool:
    return bool(raw_csrf and secrets.compare_digest(session.csrf_hash, token_digest(raw_csrf)))
