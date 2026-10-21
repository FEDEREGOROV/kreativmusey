from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, init_db
from .exports import create_export, export_path
from .knowledge import (
    DEFAULT_ESCALATION_TEXT,
    ESCALATION_KEY,
    GENERAL_TEXT_KEY,
    answer_question,
    get_escalation_text,
    import_default_knowledge,
    replace_general_knowledge,
)
from .models import (
    Admin,
    AdminSession,
    ApiCall,
    AppSetting,
    AuditEvent,
    ChatRequest,
    Conversation,
    ExportJob,
    FAQ,
    KnowledgeChunk,
    KnowledgeVersion,
    Message,
    VisitorSession,
    utcnow,
)
from .pricing import resolved_call_cost
from .security import (
    authenticate_admin,
    create_admin_session,
    get_or_create_visitor,
    resolve_admin_session,
    seed_admin,
    new_token,
    token_digest,
    verify_csrf,
)


settings = get_settings()
VISITOR_COOKIE = "cm_visitor"
ADMIN_COOKIE = "cm_admin"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_faqs(db: Session) -> None:
    if db.scalar(select(func.count(FAQ.id))):
        return
    items = [
        (
            "Как подать заявку?",
            "Заявка подаётся через портал Фонда. Материалы локальной базы относятся к сезону 2025–2026; актуальные сроки необходимо подтвердить перед публикацией.",
            "Заявка",
        ),
        (
            "Кто такой ключевой партнёр?",
            "Ключевой партнёр — представитель креативных индустрий, вместе с которым музей создаёт новый продукт. Конкретные требования проверяйте по правилам активного сезона.",
            "Партнёры",
        ),
        (
            "Какие документы нужны?",
            "Комплект документов зависит от типа организации и правил сезона. Помощник может найти нужный раздел базы, а спорный случай лучше уточнить у оператора.",
            "Документы",
        ),
    ]
    for order, (question, answer, category) in enumerate(items):
        db.add(
            FAQ(
                question=question,
                answer=answer,
                category=category,
                status="published",
                featured=True,
                display_order=order,
            )
        )
    db.commit()


def seed_app_settings(db: Session) -> None:
    changed = False
    if not db.get(AppSetting, ESCALATION_KEY):
        db.add(AppSetting(key=ESCALATION_KEY, value=DEFAULT_ESCALATION_TEXT))
        changed = True
    if not db.get(AppSetting, GENERAL_TEXT_KEY):
        db.add(AppSetting(key=GENERAL_TEXT_KEY, value=settings.general_knowledge_path.read_text(encoding="utf-8")))
        changed = True
    if changed:
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed_admin(db)
        import_default_knowledge(db)
        seed_faqs(db)
        seed_app_settings(db)
    settings.export_path.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Креативный музей API",
    version="1.0.0-local",
    description="Локальный полнофункциональный контур помощника конкурса.",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "REQUEST_ERROR", "message": str(exc.detail)}
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {**detail, "trace_id": secrets.token_hex(8), "retryable": exc.status_code >= 500}},
        headers=exc.headers,
    )


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=500)


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: str = Field(min_length=8, max_length=80)


class FeedbackBody(BaseModel):
    value: Literal["helpful", "unhelpful"] | None


class FAQBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    answer: str = Field(min_length=3, max_length=10000)
    category: str = Field(default="Общее", max_length=120)
    season: str = Field(default="2025-2026", max_length=64)
    featured: bool = False
    expected_revision: int | None = None


class ExportBody(BaseModel):
    format: Literal["csv", "json"] = "csv"
    mode: Literal["last", "all"] = "last"
    limit: int | None = Field(default=None, ge=1, le=100000)


class FAQOrderBody(BaseModel):
    ids: list[str] = Field(min_length=1)


class KnowledgeBody(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    content: str = Field(min_length=3, max_length=30000)
    layer: Literal["qa", "general"]
    section: str = Field(default="Материалы конкурса", max_length=240)
    season: str = Field(default="2025-2026", max_length=64)


class EscalationBody(BaseModel):
    value: str = Field(min_length=20, max_length=5000)
    expected_revision: int | None = None


class GeneralTextBody(BaseModel):
    value: str = Field(min_length=40, max_length=500000)
    expected_revision: int | None = None


def serialize_message(message: Message, db: Session) -> dict[str, Any]:
    source_ids = json.loads(message.source_ids_json or "[]")
    sources: list[dict[str, Any]] = []
    if source_ids:
        chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(source_ids))).all()
        chunk_map = {chunk.id: chunk for chunk in chunks}
        sources = [
            {
                "id": source_id,
                "title": chunk_map[source_id].title,
                "section": chunk_map[source_id].section,
                "url": None,
            }
            for source_id in source_ids
            if source_id in chunk_map
        ]
    return {
        "id": message.id,
        "sequence": message.sequence,
        "role": message.role,
        "text": message.text,
        "created_at": message.created_at.isoformat(),
        "result_status": message.result_status,
        "request_id": message.request_id,
        "source_ids": source_ids,
        "sources": sources,
        "feedback": message.feedback,
    }


def owned_conversation(db: Session, visitor: VisitorSession, conversation_id: str) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.visitor_session_id == visitor.id,
        )
    )
    if not conversation:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Диалог не найден"})
    return conversation


def require_visitor(request: Request, db: Session) -> VisitorSession:
    raw_token = request.cookies.get(VISITOR_COOKIE)
    if not raw_token:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Сначала создайте сессию"})
    visitor, created = get_or_create_visitor(db, raw_token)
    if created:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Сессия истекла"})
    return visitor


def require_admin(request: Request, db: Session, csrf: bool = False) -> tuple[AdminSession, Admin]:
    resolved = resolve_admin_session(db, request.cookies.get(ADMIN_COOKIE))
    if not resolved:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Требуется вход администратора"})
    session, admin = resolved
    if csrf and not verify_csrf(session, request.headers.get("x-csrf-token")):
        raise HTTPException(403, {"code": "FORBIDDEN", "message": "Некорректный CSRF-токен"})
    return session, admin


def process_chat_request(request_id: str) -> None:
    with SessionLocal() as db:
        chat_request = db.get(ChatRequest, request_id)
        if not chat_request or chat_request.state not in {"queued", "failed"}:
            return
        chat_request.state = "processing"
        db.commit()
        user_message = db.scalar(select(Message).where(Message.id == chat_request.user_message_id))
        if not user_message:
            chat_request.state = "failed"
            chat_request.error_code = "MESSAGE_NOT_FOUND"
            db.commit()
            return
        try:
            recent_messages = list(db.scalars(
                select(Message)
                .where(Message.conversation_id == chat_request.conversation_id)
                .where(Message.id != user_message.id)
                .order_by(Message.sequence.desc())
                .limit(8)
            ).all())
            history = [(message.role, message.text) for message in reversed(recent_messages)]
            result, sources, version_id = answer_question(
                db,
                request_id,
                user_message.text,
                history=history,
            )
            assistant_message = Message(
                conversation_id=chat_request.conversation_id,
                request_id=chat_request.id,
                role="assistant",
                text=result.answer,
                result_status=result.status,
                kb_version_id=version_id or None,
                source_ids_json=json.dumps([source.id for source in sources]),
            )
            db.add(assistant_message)
            chat_request.state = "completed"
            chat_request.result = result.status
            chat_request.kb_version_id = version_id or None
            chat_request.completed_at = utcnow()
            conversation = db.get(Conversation, chat_request.conversation_id)
            if conversation:
                conversation.last_message_at = utcnow()
            db.commit()
        except Exception:
            chat_request.state = "failed"
            chat_request.error_code = "PROVIDER_OR_PROCESSING_ERROR"
            chat_request.completed_at = utcnow()
            db.commit()


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    db.scalar(select(func.count(KnowledgeVersion.id)))
    return {"status": "ok", "database": "ready", "knowledge": "ready"}


@app.get("/api/health")
def compatibility_health():
    return {"status": "ok", "service": "creative-museum-api", "mode": "demo" if settings.demo_mode else "production"}


@app.get("/api/v1/public/config")
def public_config():
    return {
        "name": "Креативный музей",
        "demo": settings.demo_mode or not bool(settings.openai_api_key),
        "season": settings.active_contest_season or None,
        "legacy_season": "2025-2026",
        "support_email": settings.support_email,
        "support_phone": settings.support_phone,
        "capabilities": {"chat": True, "faq": True, "feedback": True},
    }


@app.get("/api/v1/session")
def session_endpoint(request: Request, response: Response, embed: bool = False, db: Session = Depends(get_db)):
    visitor, created_token = get_or_create_visitor(db, request.cookies.get(VISITOR_COOKIE))
    if created_token:
        response.set_cookie(
            VISITOR_COOKIE,
            created_token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="none" if embed else "lax",
            max_age=365 * 24 * 60 * 60,
        )
        if embed and settings.cookie_secure:
            name, value = response.raw_headers[-1]
            if name.lower() == b"set-cookie" and b"Partitioned" not in value:
                response.raw_headers[-1] = (name, value + b"; Partitioned")
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.visitor_session_id == visitor.id)
        .order_by(Conversation.created_at.desc())
    )
    if not conversation:
        conversation = Conversation(visitor_session_id=visitor.id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
    return {"session_id": visitor.id, "conversation_id": conversation.id}


@app.get("/api/v1/faqs")
def public_faqs(db: Session = Depends(get_db)):
    items = db.scalars(
        select(FAQ)
        .where(FAQ.status == "published")
        .order_by(FAQ.display_order, FAQ.created_at)
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "question": item.question,
                "answer": item.answer,
                "category": item.category,
                "season": item.season,
                "featured": item.featured,
            }
            for item in items
        ]
    }


@app.post("/api/v1/conversations", status_code=201)
def create_conversation(request: Request, db: Session = Depends(get_db)):
    visitor = require_visitor(request, db)
    conversation = Conversation(visitor_session_id=visitor.id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return {"id": conversation.id, "created_at": conversation.created_at.isoformat()}


@app.get("/api/v1/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str, request: Request, db: Session = Depends(get_db)):
    visitor = require_visitor(request, db)
    owned_conversation(db, visitor, conversation_id)
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.sequence)
    ).all()
    return {"items": [serialize_message(message, db) for message in messages], "next_cursor": None, "has_more": False}


@app.post("/api/v1/conversations/{conversation_id}/messages", status_code=202)
def send_message(
    conversation_id: str,
    body: MessageBody,
    request: Request,
    background: BackgroundTasks,
    idempotency_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    visitor = require_visitor(request, db)
    conversation = owned_conversation(db, visitor, conversation_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": "Введите вопрос"})
    existing = db.scalar(
        select(ChatRequest).where(
            ChatRequest.conversation_id == conversation_id,
            ChatRequest.client_message_id == body.client_message_id,
        )
    )
    if existing:
        message = db.scalar(select(Message).where(Message.id == existing.user_message_id))
        if not message or message.text != text:
            raise HTTPException(409, {"code": "CONFLICT", "message": "Ключ повторно использован для другого вопроса"})
        return {"request_id": existing.id, "message_id": existing.user_message_id, "status": existing.state, "poll_after_ms": 500}

    active = db.scalar(
        select(ChatRequest).where(
            ChatRequest.conversation_id == conversation_id,
            ChatRequest.state.in_(["queued", "processing"]),
        )
    )
    if active:
        raise HTTPException(409, {"code": "CONFLICT", "message": "Дождитесь ответа на предыдущий вопрос"})

    user_message = Message(conversation_id=conversation_id, role="user", text=text)
    db.add(user_message)
    db.flush()
    chat_request = ChatRequest(
        user_message_id=user_message.id,
        conversation_id=conversation_id,
        client_message_id=body.client_message_id,
        idempotency_key=idempotency_key or body.client_message_id,
    )
    db.add(chat_request)
    db.flush()
    user_message.request_id = chat_request.id
    conversation.last_message_at = utcnow()
    db.commit()
    background.add_task(process_chat_request, chat_request.id)
    return {"request_id": chat_request.id, "message_id": user_message.id, "status": "queued", "poll_after_ms": 500}


@app.get("/api/v1/chat-requests/{request_id}")
def chat_request_status(request_id: str, request: Request, db: Session = Depends(get_db)):
    visitor = require_visitor(request, db)
    chat_request = db.get(ChatRequest, request_id)
    if not chat_request:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Запрос не найден"})
    owned_conversation(db, visitor, chat_request.conversation_id)
    assistant = db.scalar(
        select(Message).where(Message.request_id == request_id, Message.role == "assistant")
    )
    return {
        "id": chat_request.id,
        "status": chat_request.state,
        "result": chat_request.result,
        "assistant_message": serialize_message(assistant, db) if assistant else None,
        "error": {"code": chat_request.error_code, "message": "Не удалось получить ответ"} if chat_request.error_code else None,
    }


@app.post("/api/v1/chat-requests/{request_id}/retry", status_code=202)
def retry_chat(request_id: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    visitor = require_visitor(request, db)
    chat_request = db.get(ChatRequest, request_id)
    if not chat_request:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Запрос не найден"})
    owned_conversation(db, visitor, chat_request.conversation_id)
    if chat_request.state != "failed":
        raise HTTPException(409, {"code": "CONFLICT", "message": "Повтор доступен только после ошибки"})
    chat_request.state = "queued"
    chat_request.error_code = None
    db.commit()
    background.add_task(process_chat_request, chat_request.id)
    return {"request_id": chat_request.id, "status": "queued", "poll_after_ms": 500}


@app.put("/api/v1/messages/{message_id}/feedback")
def set_feedback(message_id: str, body: FeedbackBody, request: Request, db: Session = Depends(get_db)):
    visitor = require_visitor(request, db)
    message = db.scalar(select(Message).where(Message.id == message_id, Message.role == "assistant"))
    if not message:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Ответ не найден"})
    owned_conversation(db, visitor, message.conversation_id)
    message.feedback = body.value
    db.commit()
    return {"message_id": message.id, "value": message.feedback}


@app.post("/api/v1/admin/auth/login")
def admin_login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    admin = authenticate_admin(db, body.username, body.password)
    if not admin:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Неверный логин или пароль"})
    _, raw_token, csrf_token = create_admin_session(db, admin)
    response.set_cookie(
        ADMIN_COOKIE,
        raw_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    db.add(AuditEvent(admin_id=admin.id, action="admin.login", entity_type="admin", entity_id=admin.id))
    db.commit()
    return {"username": admin.username, "csrf_token": csrf_token, "demo": settings.demo_mode}


@app.get("/api/v1/admin/auth/me")
def admin_me(request: Request, db: Session = Depends(get_db)):
    session, admin = require_admin(request, db)
    csrf_token = new_token()
    session.csrf_hash = token_digest(csrf_token)
    db.commit()
    return {
        "username": admin.username,
        "permissions": ["conversations", "usage", "faqs", "exports"],
        "csrf_token": csrf_token,
    }


@app.post("/api/v1/admin/auth/logout")
def admin_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    session, admin = require_admin(request, db, csrf=True)
    db.delete(session)
    db.add(AuditEvent(admin_id=admin.id, action="admin.logout", entity_type="admin", entity_id=admin.id))
    db.commit()
    response.delete_cookie(ADMIN_COOKIE)
    return {"ok": True}


@app.get("/api/v1/admin/conversations")
def admin_conversations(request: Request, q: str = "", limit: int = 50, db: Session = Depends(get_db)):
    require_admin(request, db)
    query = select(Conversation).order_by(Conversation.last_message_at.desc()).limit(min(max(limit, 1), 100))
    conversations = db.scalars(query).all()
    items: list[dict[str, Any]] = []
    for conversation in conversations:
        messages = db.scalars(
            select(Message).where(Message.conversation_id == conversation.id).order_by(Message.sequence)
        ).all()
        if q and not any(q.casefold() in message.text.casefold() for message in messages):
            continue
        last_question = next((message.text for message in reversed(messages) if message.role == "user"), "")
        last_answer = next((message for message in reversed(messages) if message.role == "assistant"), None)
        items.append(
            {
                "id": conversation.id,
                "created_at": conversation.created_at.isoformat(),
                "last_message_at": conversation.last_message_at.isoformat(),
                "last_question": last_question[:240],
                "message_count": len(messages),
                "result": last_answer.result_status if last_answer else None,
                "feedback": last_answer.feedback if last_answer else None,
                "channel": conversation.channel,
            }
        )
    return {"items": items, "next_cursor": None, "has_more": False}


@app.get("/api/v1/admin/conversations/{conversation_id}")
def admin_conversation(conversation_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Диалог не найден"})
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.sequence)
    ).all()
    return {
        "id": conversation.id,
        "channel": conversation.channel,
        "created_at": conversation.created_at.isoformat(),
        "messages": [serialize_message(message, db) for message in messages],
    }


@app.get("/api/v1/admin/usage/summary")
def usage_summary(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    calls = db.scalars(select(ApiCall).order_by(ApiCall.created_at.desc())).all()
    questions = db.scalar(select(func.count(Message.id)).where(Message.role == "user")) or 0
    completed = db.scalar(select(func.count(ChatRequest.id)).where(ChatRequest.state == "completed")) or 0
    failed = db.scalar(select(func.count(ChatRequest.id)).where(ChatRequest.state == "failed")) or 0
    provider_calls = [call for call in calls if call.model != "local-retrieval"]
    chat_calls = [call for call in provider_calls if call.purpose == "chat"]
    embedding_calls = [call for call in provider_calls if call.purpose == "knowledge-embedding"]
    priced = [(call, resolved_call_cost(call)) for call in provider_calls]
    known_cost = sum(cost or 0 for _, cost in priced)
    chat_cost = sum(resolved_call_cost(call) or 0 for call in chat_calls)
    embedding_cost = sum(resolved_call_cost(call) or 0 for call in embedding_calls)
    return {
        "questions": questions,
        "successful_answers": completed,
        "api_calls": len(provider_calls),
        "local_operations": len(calls) - len(provider_calls),
        "errors": failed,
        "input_tokens": sum(call.input_tokens or 0 for call in provider_calls),
        "output_tokens": sum(call.output_tokens or 0 for call in provider_calls),
        "cached_tokens": sum(call.cached_tokens or 0 for call in chat_calls),
        "reasoning_tokens": sum(call.reasoning_tokens or 0 for call in chat_calls),
        "chat_input_tokens": sum(call.input_tokens or 0 for call in chat_calls),
        "chat_output_tokens": sum(call.output_tokens or 0 for call in chat_calls),
        "embedding_input_tokens": sum(call.input_tokens or 0 for call in embedding_calls),
        "chat_cost": f"{chat_cost:.6f}",
        "embedding_cost": f"{embedding_cost:.6f}",
        "known_cost": f"{known_cost:.6f}",
        "currency": "USD",
        "unknown_count": sum(1 for _, cost in priced if cost is None),
        "model": settings.openai_model,
        "demo": settings.demo_mode or not bool(settings.openai_api_key),
    }


@app.get("/api/v1/admin/usage/daily")
def usage_daily(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    calls = db.scalars(select(ApiCall).order_by(ApiCall.created_at)).all()
    grouped: dict[str, dict[str, Any]] = {}
    for call in calls:
        if call.model == "local-retrieval":
            continue
        day = call.created_at.date().isoformat()
        row = grouped.setdefault(day, {"date": day, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0})
        row["calls"] += 1
        row["input_tokens"] += call.input_tokens or 0
        row["output_tokens"] += call.output_tokens or 0
        row["cost"] += resolved_call_cost(call) or 0
    return {"items": [{**row, "cost": f"{row['cost']:.6f}"} for row in grouped.values()]}


@app.get("/api/v1/admin/faqs")
def admin_faqs(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    items = db.scalars(select(FAQ).order_by(FAQ.display_order, FAQ.created_at)).all()
    return {
        "items": [
            {
                "id": item.id,
                "question": item.question,
                "answer": item.answer,
                "category": item.category,
                "season": item.season,
                "status": item.status,
                "featured": item.featured,
                "display_order": item.display_order,
                "revision": item.revision,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in items
        ]
    }


@app.post("/api/v1/admin/faqs", status_code=201)
def create_faq(body: FAQBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    max_order = db.scalar(select(func.max(FAQ.display_order))) or 0
    item = FAQ(
        question=body.question,
        answer=body.answer,
        category=body.category,
        season=body.season,
        featured=body.featured,
        display_order=max_order + 1,
        status="draft",
    )
    db.add(item)
    db.add(AuditEvent(admin_id=admin.id, action="faq.create", entity_type="faq", entity_id=item.id))
    db.commit()
    db.refresh(item)
    return {"id": item.id, "revision": item.revision, "status": item.status}


@app.patch("/api/v1/admin/faqs/{faq_id}")
def update_faq(faq_id: str, body: FAQBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    item = db.get(FAQ, faq_id)
    if not item:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "FAQ не найден"})
    if body.expected_revision is not None and body.expected_revision != item.revision:
        raise HTTPException(409, {"code": "CONFLICT", "message": "FAQ уже изменён в другой вкладке"})
    item.question = body.question
    item.answer = body.answer
    item.category = body.category
    item.season = body.season
    item.featured = body.featured
    item.revision += 1
    item.status = "draft"
    db.add(AuditEvent(admin_id=admin.id, action="faq.update", entity_type="faq", entity_id=item.id))
    db.commit()
    return {"id": item.id, "revision": item.revision, "status": item.status}


@app.post("/api/v1/admin/faqs/{faq_id}/publish")
def publish_faq(faq_id: str, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    item = db.get(FAQ, faq_id)
    if not item:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "FAQ не найден"})
    item.status = "published"
    item.revision += 1
    db.add(AuditEvent(admin_id=admin.id, action="faq.publish", entity_type="faq", entity_id=item.id))
    db.commit()
    return {"id": item.id, "revision": item.revision, "status": item.status}


@app.post("/api/v1/admin/faqs/{faq_id}/hide")
def hide_faq(faq_id: str, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    item = db.get(FAQ, faq_id)
    if not item:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "FAQ не найден"})
    item.status = "hidden"
    item.revision += 1
    db.add(AuditEvent(admin_id=admin.id, action="faq.hide", entity_type="faq", entity_id=item.id))
    db.commit()
    return {"id": item.id, "revision": item.revision, "status": item.status}


@app.put("/api/v1/admin/faqs/order")
def order_faqs(body: FAQOrderBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    items = db.scalars(select(FAQ)).all()
    if set(body.ids) != {item.id for item in items} or len(body.ids) != len(items):
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": "Передайте все FAQ ровно по одному разу"})
    item_map = {item.id: item for item in items}
    for order, item_id in enumerate(body.ids):
        item_map[item_id].display_order = order
    db.add(AuditEvent(admin_id=admin.id, action="faq.reorder", entity_type="faq"))
    db.commit()
    return {"ids": body.ids}


def serialize_knowledge_item(item: KnowledgeChunk) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "layer": item.topic,
        "section": item.section,
        "season": item.season,
        "source_label": item.source_label,
        "status": item.approval_status,
        "display_order": item.display_order,
    }


@app.get("/api/v1/admin/knowledge")
def admin_knowledge(
    request: Request,
    layer: Literal["qa", "general"] = "qa",
    q: str = "",
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    statement = (
        select(KnowledgeChunk)
        .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeChunk.version_id)
        .where(KnowledgeVersion.status == "published", KnowledgeChunk.topic == layer)
        .order_by(KnowledgeChunk.display_order, KnowledgeChunk.title)
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(KnowledgeChunk.title.ilike(pattern) | KnowledgeChunk.content.ilike(pattern))
    items = db.scalars(statement).all()
    return {"items": [serialize_knowledge_item(item) for item in items], "count": len(items)}


@app.post("/api/v1/admin/knowledge", status_code=201)
def create_knowledge(body: KnowledgeBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published"))
    if not version:
        version = import_default_knowledge(db)
    item = KnowledgeChunk(
        version_id=version.id,
        title=body.title.strip(),
        content=body.content.strip(),
        section=body.section.strip() or "Материалы конкурса",
        topic=body.layer,
        season=body.season.strip() or "2025-2026",
        source_label="База вопросов и ответов" if body.layer == "qa" else "Общая база конкурса",
        approval_status="approved",
        display_order=db.scalar(select(func.coalesce(func.max(KnowledgeChunk.display_order), -1)).where(
            KnowledgeChunk.version_id == version.id,
            KnowledgeChunk.topic == body.layer,
        )) + 1,
    )
    db.add(item)
    db.flush()
    db.add(AuditEvent(admin_id=admin.id, action="knowledge.create", entity_type="knowledge", entity_id=item.id))
    db.commit()
    db.refresh(item)
    return serialize_knowledge_item(item)


@app.patch("/api/v1/admin/knowledge/{item_id}")
def update_knowledge(item_id: str, body: KnowledgeBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    item = db.get(KnowledgeChunk, item_id)
    if not item:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Материал не найден"})
    item.title = body.title.strip()
    item.content = body.content.strip()
    item.section = body.section.strip() or "Материалы конкурса"
    item.topic = body.layer
    item.season = body.season.strip() or "2025-2026"
    item.source_label = "База вопросов и ответов" if body.layer == "qa" else "Общая база конкурса"
    db.add(AuditEvent(admin_id=admin.id, action="knowledge.update", entity_type="knowledge", entity_id=item.id))
    db.commit()
    db.refresh(item)
    return serialize_knowledge_item(item)


@app.delete("/api/v1/admin/knowledge/{item_id}")
def delete_knowledge(item_id: str, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    item = db.get(KnowledgeChunk, item_id)
    if not item:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Материал не найден"})
    db.add(AuditEvent(admin_id=admin.id, action="knowledge.delete", entity_type="knowledge", entity_id=item.id))
    db.delete(item)
    db.commit()
    return {"deleted": True, "id": item_id}


@app.post("/api/v1/admin/knowledge/{item_id}/{action}")
def change_knowledge_status(
    item_id: str,
    action: Literal["activate", "hide"],
    request: Request,
    db: Session = Depends(get_db),
):
    _, admin = require_admin(request, db, csrf=True)
    item = db.get(KnowledgeChunk, item_id)
    if not item:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Материал не найден"})
    item.approval_status = "approved" if action == "activate" else "hidden"
    db.add(AuditEvent(admin_id=admin.id, action=f"knowledge.{action}", entity_type="knowledge", entity_id=item.id))
    db.commit()
    return serialize_knowledge_item(item)


@app.get("/api/v1/admin/knowledge/general-text")
def general_knowledge_text(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    setting = db.get(AppSetting, GENERAL_TEXT_KEY)
    value = setting.value if setting else settings.general_knowledge_path.read_text(encoding="utf-8")
    return {"value": value, "revision": setting.revision if setting else 0}


@app.put("/api/v1/admin/knowledge/general-text")
def update_general_knowledge_text(body: GeneralTextBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    setting = db.get(AppSetting, GENERAL_TEXT_KEY)
    if not setting:
        setting = AppSetting(key=GENERAL_TEXT_KEY, value=body.value)
        db.add(setting)
    else:
        if body.expected_revision is not None and body.expected_revision != setting.revision:
            raise HTTPException(409, {"code": "CONFLICT", "message": "Общая база уже изменена в другой вкладке"})
        setting.value = body.value
        setting.revision += 1
    chunk_count = replace_general_knowledge(db, body.value)
    db.add(AuditEvent(admin_id=admin.id, action="knowledge.general.update", entity_type="setting", entity_id=GENERAL_TEXT_KEY))
    db.commit()
    db.refresh(setting)
    return {"value": setting.value, "revision": setting.revision, "chunk_count": chunk_count}


@app.get("/api/v1/admin/knowledge/settings/escalation")
def escalation_setting(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    setting = db.get(AppSetting, ESCALATION_KEY)
    return {
        "value": get_escalation_text(db),
        "revision": setting.revision if setting else 0,
    }


@app.put("/api/v1/admin/knowledge/settings/escalation")
def update_escalation_setting(body: EscalationBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    setting = db.get(AppSetting, ESCALATION_KEY)
    if not setting:
        setting = AppSetting(key=ESCALATION_KEY, value=body.value.strip())
        db.add(setting)
    else:
        if body.expected_revision is not None and body.expected_revision != setting.revision:
            raise HTTPException(409, {"code": "CONFLICT", "message": "Текст уже изменён в другой вкладке"})
        setting.value = body.value.strip()
        setting.revision += 1
    db.add(AuditEvent(admin_id=admin.id, action="knowledge.escalation.update", entity_type="setting", entity_id=ESCALATION_KEY))
    db.commit()
    db.refresh(setting)
    return {"value": setting.value, "revision": setting.revision}


@app.post("/api/v1/admin/exports", status_code=202)
def start_export(body: ExportBody, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db, csrf=True)
    if body.mode == "all" and body.limit is not None:
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": "Для режима all не задавайте limit"})
    if body.mode == "last" and body.limit is None:
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": "Для режима last укажите limit"})
    job = create_export(db, admin.id, body.format, body.mode, body.limit if body.mode == "last" else None)
    db.add(AuditEvent(admin_id=admin.id, action="export.create", entity_type="export", entity_id=job.id))
    db.commit()
    return {
        "id": job.id,
        "state": job.state,
        "actual_count": job.actual_count,
        "expires_at": job.expires_at.isoformat(),
    }


@app.get("/api/v1/admin/exports/{job_id}")
def export_status(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    job = db.get(ExportJob, job_id)
    if not job:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Экспорт не найден"})
    state = "expired" if job.expires_at <= utcnow() else job.state
    return {
        "id": job.id,
        "state": state,
        "format": job.format,
        "actual_count": job.actual_count,
        "snapshot_watermark": job.snapshot_watermark,
        "created_at": job.created_at.isoformat(),
        "expires_at": job.expires_at.isoformat(),
    }


@app.get("/api/v1/admin/exports/{job_id}/download")
def download_export(job_id: str, request: Request, db: Session = Depends(get_db)):
    _, admin = require_admin(request, db)
    job = db.get(ExportJob, job_id)
    if not job:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Экспорт не найден"})
    path = export_path(job)
    if not path:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Файл недоступен или срок истёк"})
    db.add(AuditEvent(admin_id=admin.id, action="export.download", entity_type="export", entity_id=job.id))
    db.commit()
    return FileResponse(path, filename=f"creative-museum-messages.{job.format}")


@app.get("/api/v1/admin/system")
def admin_system(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published"))
    chunks = db.scalar(select(func.count(KnowledgeChunk.id))) or 0
    return {
        "environment": settings.app_env,
        "demo": settings.demo_mode or not bool(settings.openai_api_key),
        "database": settings.database_url.split(":", 1)[0],
        "model": settings.openai_model,
        "knowledge_version": version.id if version else None,
        "knowledge_source": version.name if version else None,
        "knowledge_chunks": chunks,
        "active_season": settings.active_contest_season or None,
    }
