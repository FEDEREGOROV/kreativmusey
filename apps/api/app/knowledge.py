from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ApiCall, AppSetting, KnowledgeChunk, KnowledgeVersion


settings = get_settings()
WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}", re.IGNORECASE)
STOPWORDS = {
    "как", "что", "это", "для", "или", "может", "быть", "кто", "какие", "какой",
    "при", "про", "могу", "можно", "нужно", "нужны", "есть", "конкурс", "конкурса",
}
ESCALATION_KEY = "chat.escalation_text"
GENERAL_TEXT_KEY = "knowledge.general_text"
DEFAULT_ESCALATION_TEXT = (
    "Если я не смог ответить на этот вопрос или вы хотите узнать больше, то коллеги из команды "
    "оператора Конкурса «Креативный музей» с удовольствием подскажут!\n\n"
    "Пожалуйста, отправьте ваш запрос на почту creativemuseum@futuretime.ru или позвоните "
    "на горячую линию 8-800-600-79-31."
)


@dataclass
class SearchHit:
    chunk: KnowledgeChunk
    score: float


class ModelAnswer(BaseModel):
    status: Literal["answered", "clarification", "no_answer", "off_topic"]
    answer: str
    source_ids: list[str]
    follow_up: str | None = None


def _entries(source_text: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    section = "Материалы конкурса"
    buffer: list[str] = []
    in_entry = False

    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not in_entry and line.startswith("["):
            buffer = [line[1:]]
            in_entry = True
        elif in_entry:
            buffer.append(line)
        elif line:
            section = line[:240]

        if in_entry and line.endswith("]"):
            buffer[-1] = buffer[-1][:-1]
            joined = "\n".join(part for part in buffer if part).strip()
            title, separator, content = joined.partition(":")
            if separator and title.strip() and content.strip():
                entries.append((section, title.strip()[:500], content.strip()))
            buffer = []
            in_entry = False

    if buffer:
        joined = "\n".join(buffer).strip()
        title, separator, content = joined.partition(":")
        if separator and title.strip() and content.strip():
            entries.append((section, title.strip()[:500], content.strip()))
    return entries


def _general_entries(source_text: str) -> list[tuple[str, str, str]]:
    cleaned = source_text.replace("\ufeff", "").strip()
    blocks = [block.strip().strip('"').strip() for block in re.split(r'\n\s*"(?=[А-ЯЁA-Z])|\n{3,}', cleaned)]
    entries: list[tuple[str, str, str]] = []
    for index, block in enumerate(blocks, start=1):
        if len(block) < 40:
            continue
        compact = re.sub(r"\s+", " ", block)
        title = re.split(r"[.!?]\s", compact, maxsplit=1)[0][:160].strip(' "')
        if len(title) < 12:
            title = f"Общий материал {index}"
        entries.append(("Общая база конкурса", title, block))
    return entries


def get_escalation_text(db: Session) -> str:
    setting = db.get(AppSetting, ESCALATION_KEY)
    return setting.value if setting and setting.value.strip() else DEFAULT_ESCALATION_TEXT


def replace_general_knowledge(db: Session, source_text: str) -> int:
    version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published"))
    if not version:
        raise ValueError("Опубликованная версия базы не найдена")
    db.execute(delete(KnowledgeChunk).where(
        KnowledgeChunk.version_id == version.id,
        KnowledgeChunk.topic == "general",
    ))
    entries = _general_entries(source_text)
    for display_order, (section, title, content) in enumerate(entries):
        db.add(KnowledgeChunk(
            version_id=version.id,
            title=title,
            content=content,
            section=section,
            topic="general",
            season=version.season,
            source_label="Общая база конкурса",
            approval_status="approved",
            display_order=display_order,
        ))
    return len(entries)


def replace_qa_knowledge(db: Session, source_text: str) -> int:
    version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published"))
    if not version:
        raise ValueError("Опубликованная версия базы не найдена")
    db.execute(delete(KnowledgeChunk).where(
        KnowledgeChunk.version_id == version.id,
        KnowledgeChunk.topic == "qa",
    ))
    entries = _entries(source_text)
    for display_order, (section, title, content) in enumerate(entries):
        db.add(KnowledgeChunk(
            version_id=version.id,
            title=title,
            content=content,
            section=section,
            topic="qa",
            season=version.season,
            source_label="База вопросов и ответов",
            approval_status="approved",
            display_order=display_order,
        ))
    return len(entries)


def import_knowledge_file(
    db: Session,
    path: Path,
    season: str,
    status: str = "draft",
) -> tuple[KnowledgeVersion, int]:
    source_bytes = path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    existing = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.source_hash == source_hash))
    if existing:
        count = len(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.version_id == existing.id)).all())
        return existing, count

    version = KnowledgeVersion(
        name=path.name,
        season=season,
        status=status,
        source_hash=source_hash,
    )
    db.add(version)
    db.flush()
    entries = _entries(source_bytes.decode("utf-8"))
    for display_order, (section, title, content) in enumerate(entries):
        db.add(
            KnowledgeChunk(
                version_id=version.id,
                title=title,
                content=content,
                section=section,
                topic="qa",
                season=season,
                source_label="База вопросов и ответов",
                approval_status="approved" if status == "published" else "draft",
                display_order=display_order,
            )
        )
    db.commit()
    db.refresh(version)
    return version, len(entries)


def import_default_knowledge(db: Session) -> KnowledgeVersion:
    qa_bytes = settings.knowledge_path.read_bytes()
    general_bytes = settings.general_knowledge_path.read_bytes()
    source_hash = hashlib.sha256(qa_bytes + b"\0" + general_bytes).hexdigest()
    existing = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.source_hash == source_hash))
    if existing:
        if not db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published")):
            existing.status = "published"
            db.commit()
        return existing
    db.execute(update(KnowledgeVersion).where(KnowledgeVersion.status == "published").values(status="archived"))
    version = KnowledgeVersion(
        name="База вопросов и ответов + общая база",
        season="2025-2026",
        status="published",
        source_hash=source_hash,
    )
    db.add(version)
    db.flush()
    for display_order, (section, title, content) in enumerate(_entries(qa_bytes.decode("utf-8"))):
        db.add(KnowledgeChunk(
            version_id=version.id,
            title=title,
            content=content,
            section=section,
            topic="qa",
            season="2025-2026",
            source_label="База вопросов и ответов",
            approval_status="approved",
            display_order=display_order,
        ))
    for display_order, (section, title, content) in enumerate(_general_entries(general_bytes.decode("utf-8"))):
        db.add(KnowledgeChunk(
            version_id=version.id,
            title=title,
            content=content,
            section=section,
            topic="general",
            season="2025-2026",
            source_label="Общая база конкурса",
            approval_status="approved",
            display_order=display_order,
        ))
    db.commit()
    db.refresh(version)
    return version


def publish_version(db: Session, version_id: str) -> KnowledgeVersion:
    version = db.get(KnowledgeVersion, version_id)
    if not version:
        raise ValueError("Версия базы не найдена")
    db.execute(update(KnowledgeVersion).where(KnowledgeVersion.status == "published").values(status="archived"))
    version.status = "published"
    chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)).all()
    for chunk in chunks:
        chunk.approval_status = "legacy" if not settings.active_contest_season else "approved"
    db.commit()
    db.refresh(version)
    return version


def search_knowledge(db: Session, query: str, limit: int = 5, topic: str | None = None) -> list[SearchHit]:
    tokens = [token.casefold() for token in WORD_RE.findall(query) if token.casefold() not in STOPWORDS]
    if not tokens:
        tokens = [token.casefold() for token in WORD_RE.findall(query)]
    statement = (
        select(KnowledgeChunk)
        .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeChunk.version_id)
        .where(KnowledgeVersion.status == "published")
        .where(KnowledgeChunk.approval_status != "hidden")
    )
    if topic:
        statement = statement.where(KnowledgeChunk.topic == topic)
    chunks = db.scalars(statement).all()
    hits: list[SearchHit] = []
    normalized_query = " ".join(WORD_RE.findall(query.casefold()))
    for chunk in chunks:
        title = chunk.title.casefold()
        normalized_title = " ".join(WORD_RE.findall(title))
        content = chunk.content.casefold()
        matched = sum((4 if token in title else 0) + (1 if token in content else 0) for token in tokens)
        phrase_bonus = 6 if normalized_query in normalized_title or normalized_title in normalized_query else 0
        completeness_bonus = min(len(chunk.content) / 300, 4)
        score = float(matched + phrase_bonus + completeness_bonus)
        if score > 0:
            hits.append(SearchHit(chunk=chunk, score=score))
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.title))[:limit]


def _qa_match_confident(question: str, hits: list[SearchHit]) -> bool:
    if not hits:
        return False
    query_tokens = {token.casefold() for token in WORD_RE.findall(question) if token.casefold() not in STOPWORDS}
    title_tokens = {token.casefold() for token in WORD_RE.findall(hits[0].chunk.title) if token.casefold() not in STOPWORDS}
    if not query_tokens:
        query_tokens = {token.casefold() for token in WORD_RE.findall(question)}
    overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens))
    normalized_query = " ".join(WORD_RE.findall(question.casefold()))
    normalized_title = " ".join(WORD_RE.findall(hits[0].chunk.title.casefold()))
    return normalized_query in normalized_title or normalized_title in normalized_query or overlap >= 0.5


def _is_off_topic(question: str) -> bool:
    lowered = question.casefold()
    allowed = (
        "конкурс", "заяв", "музей", "грант", "бюджет", "партн", "проект", "документ",
        "участв", "команд", "аудитор", "социальн", "креатив", "фонд", "срок", "руковод",
        "продукт", "результат", "риск", "ресурс", "привет", "здравств",
    )
    return not any(token in lowered for token in allowed)


def _demo_answer(db: Session, question: str, qa_hits: list[SearchHit], general_hits: list[SearchHit]) -> ModelAnswer:
    if any(word in question.casefold() for word in ("привет", "здравств")):
        return ModelAnswer(
            status="answered",
            answer="Здравствуйте! Я помогу разобраться с участием, заявкой, бюджетом и партнёрами конкурса «Креативный музей».",
            source_ids=[],
        )
    if _qa_match_confident(question, qa_hits):
        best = qa_hits[0].chunk
    elif general_hits and general_hits[0].score >= 4:
        best = general_hits[0].chunk
    else:
        return ModelAnswer(
            status="off_topic" if _is_off_topic(question) else "no_answer",
            answer=get_escalation_text(db),
            source_ids=[],
        )
    answer = best.content.strip()
    if len(answer) > 2200:
        answer = answer[:2200].rsplit(" ", 1)[0] + "…"
    return ModelAnswer(status="answered", answer=answer, source_ids=[best.id])


def answer_question(db: Session, request_id: str, question: str) -> tuple[ModelAnswer, list[KnowledgeChunk], str]:
    qa_hits = search_knowledge(db, question, topic="qa")
    general_hits = search_knowledge(db, question, topic="general")
    use_qa = _qa_match_confident(question, qa_hits)
    hits = qa_hits[:3] if use_qa else general_hits[:5]
    version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published"))
    version_id = version.id if version else ""
    if settings.demo_mode or not settings.openai_api_key:
        result = _demo_answer(db, question, qa_hits, general_hits)
        selected = [hit.chunk for hit in hits if hit.chunk.id in result.source_ids]
        db.add(
            ApiCall(
                request_id=request_id,
                purpose="chat-demo-retrieval",
                model="local-retrieval",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
                cost_status="known",
                duration_ms=0,
            )
        )
        db.commit()
        return result, selected, version_id

    context = "\n\n".join(
        f"SOURCE_ID: {hit.chunk.id}\nTITLE: {hit.chunk.title}\nSECTION: {hit.chunk.section}\nCONTENT: {hit.chunk.content}"
        for hit in hits
    )
    layer_instruction = (
        "Найден подходящий вопрос в приоритетной базе Q&A: используй готовый ответ без изменения фактов. "
        if use_qa else
        "Ответ сформируй по общей базе: сначала дай прямой вывод, затем кратко структурируй детали списком, если это уместно. "
    )
    prompt = (
        layer_instruction + "Отвечай только по переданным материалам конкурса. Не выдумывай правила и не используй внешние знания. "
        "Если данных недостаточно, верни no_answer. Для неоднозначного вопроса верни clarification. "
        "Для сторонней темы верни off_topic. source_ids могут содержать только переданные SOURCE_ID. "
        "Старые даты явно помечай как относящиеся к сезону 2025–2026."
    )
    started = time.perf_counter()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.parse(
        model=settings.openai_model,
        reasoning={"effort": settings.openai_reasoning_effort},
        max_output_tokens=settings.openai_max_output_tokens,
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"ВОПРОС:\n{question}\n\nМАТЕРИАЛЫ:\n{context}"},
        ],
        text_format=ModelAnswer,
    )
    parsed = response.output_parsed
    if parsed.status in {"no_answer", "off_topic"}:
        parsed.answer = get_escalation_text(db)
    allowed_ids = {hit.chunk.id for hit in hits}
    parsed.source_ids = [source_id for source_id in parsed.source_ids if source_id in allowed_ids]
    selected = [hit.chunk for hit in hits if hit.chunk.id in parsed.source_ids]
    usage = response.usage
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    db.add(
        ApiCall(
            request_id=request_id,
            purpose="chat",
            model=settings.openai_model,
            provider_response_id=response.id,
            input_tokens=getattr(usage, "input_tokens", None),
            cached_tokens=getattr(input_details, "cached_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            reasoning_tokens=getattr(output_details, "reasoning_tokens", None),
            cost_usd=None,
            cost_status="unknown",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    db.commit()
    return parsed, selected, version_id
