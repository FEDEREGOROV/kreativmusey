from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from collections import Counter
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
logger = logging.getLogger(__name__)
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
HELP_ANSWER = (
    "Я помогаю разобраться с конкурсом «Креативный музей». Сначала ищу подходящий готовый ответ "
    "в базе вопросов и ответов, затем проверяю общие материалы конкурса и формулирую ответ только "
    "по найденным официальным данным. Можете спросить об участниках, документах, заявке, бюджете, "
    "команде, партнёрах и сроках. Если подтверждённой информации не найдётся, я предложу обратиться "
    "к команде оператора конкурса."
)
_embedding_cache: dict[str, list[float]] = {}
_embedding_lock = threading.Lock()


@dataclass
class SearchHit:
    chunk: KnowledgeChunk
    score: float
    lexical_score: float = 0.0
    semantic_score: float | None = None


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
    chunks = _published_chunks(db, topic)
    return _lexical_hits(chunks, query, limit)


def _published_chunks(db: Session, topic: str | None = None) -> list[KnowledgeChunk]:
    statement = (
        select(KnowledgeChunk)
        .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeChunk.version_id)
        .where(KnowledgeVersion.status == "published")
        .where(KnowledgeChunk.approval_status != "hidden")
    )
    if topic:
        statement = statement.where(KnowledgeChunk.topic == topic)
    return list(db.scalars(statement).all())


def _query_tokens(query: str) -> list[str]:
    tokens = [token.casefold() for token in WORD_RE.findall(query) if token.casefold() not in STOPWORDS]
    return tokens or [token.casefold() for token in WORD_RE.findall(query)]


def _token_matches(token: str, words: set[str]) -> bool:
    if token in words:
        return True
    # A small morphology fallback for Russian word endings. It is deliberately
    # conservative so generic words do not drown out a distinctive term.
    return len(token) >= 7 and any(len(word) >= 7 and word[:6] == token[:6] for word in words)


def _lexical_hits(chunks: list[KnowledgeChunk], query: str, limit: int) -> list[SearchHit]:
    tokens = list(dict.fromkeys(_query_tokens(query)))
    if not chunks or not tokens:
        return []

    tokenized: list[tuple[KnowledgeChunk, set[str], set[str]]] = []
    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        title_words = {word.casefold() for word in WORD_RE.findall(chunk.title)}
        content_words = {word.casefold() for word in WORD_RE.findall(chunk.content)}
        all_words = title_words | content_words
        tokenized.append((chunk, title_words, content_words))
        for token in tokens:
            if _token_matches(token, all_words):
                document_frequency[token] += 1

    hits: list[SearchHit] = []
    normalized_query = " ".join(WORD_RE.findall(query.casefold()))
    document_count = len(chunks)
    for chunk, title_words, content_words in tokenized:
        title_matches = [token for token in tokens if _token_matches(token, title_words)]
        content_matches = [token for token in tokens if _token_matches(token, content_words)]
        score = 0.0
        title_idfs: list[float] = []
        for token in set(title_matches + content_matches):
            idf = math.log((document_count + 1) / (document_frequency[token] + 1)) + 1
            weighted_idf = idf ** 1.7
            if token in title_matches:
                score += 4.0 * weighted_idf
                title_idfs.append(weighted_idf)
            if token in content_matches:
                score += 1.0 * weighted_idf
        if title_idfs:
            # The most distinctive title term is often the actual intent
            # (for example, "самозанятый") and must outweigh several
            # generic words such as "подать заявку".
            score += 8.0 * max(title_idfs)

        normalized_title = " ".join(WORD_RE.findall(chunk.title.casefold()))
        matched_share = len(set(title_matches)) / max(1, len(tokens))
        if normalized_query and (
            normalized_query in normalized_title or normalized_title in normalized_query
        ):
            score += 5.0
        elif matched_share >= 0.75:
            score += 2.0
        if score > 0:
            hits.append(SearchHit(chunk=chunk, score=score, lexical_score=score))
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.title))[:limit]


def _embedding_text(chunk: KnowledgeChunk) -> str:
    return f"{chunk.title}\n{chunk.section}\n{chunk.content}"[:8000]


def _embedding_key(chunk: KnowledgeChunk) -> str:
    payload = f"{settings.openai_embedding_model}\0{chunk.id}\0{_embedding_text(chunk)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _record_embedding_call(db: Session, request_id: str, response, started: float) -> None:
    usage = getattr(response, "usage", None)
    db.add(ApiCall(
        request_id=request_id,
        purpose="knowledge-embedding",
        model=settings.openai_embedding_model,
        input_tokens=getattr(usage, "prompt_tokens", None) or getattr(usage, "total_tokens", None),
        output_tokens=0,
        cost_usd=None,
        cost_status="unknown",
        duration_ms=int((time.perf_counter() - started) * 1000),
    ))


def _hybrid_search(
    db: Session,
    request_id: str,
    query: str,
    limit: int,
    topic: str,
) -> list[SearchHit]:
    chunks = _published_chunks(db, topic)
    lexical = _lexical_hits(chunks, query, len(chunks))
    lexical_by_id = {hit.chunk.id: hit.lexical_score for hit in lexical}
    if settings.demo_mode or not settings.openai_api_key or not chunks:
        return lexical[:limit]

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        missing: list[KnowledgeChunk] = []
        with _embedding_lock:
            for chunk in chunks:
                if _embedding_key(chunk) not in _embedding_cache:
                    missing.append(chunk)

        # Batch the initial index build to stay well below the embeddings API
        # per-request token limit even when administrators add long answers.
        for offset in range(0, len(missing), 48):
            batch = missing[offset:offset + 48]
            started = time.perf_counter()
            response = client.embeddings.create(
                model=settings.openai_embedding_model,
                input=[_embedding_text(chunk) for chunk in batch],
            )
            _record_embedding_call(db, request_id, response, started)
            with _embedding_lock:
                for chunk, item in zip(batch, response.data):
                    _embedding_cache[_embedding_key(chunk)] = item.embedding

        started = time.perf_counter()
        query_response = client.embeddings.create(model=settings.openai_embedding_model, input=query)
        _record_embedding_call(db, request_id, query_response, started)
        query_vector = query_response.data[0].embedding
        maximum_lexical = max(lexical_by_id.values(), default=1.0)
        hybrid: list[SearchHit] = []
        with _embedding_lock:
            for chunk in chunks:
                vector = _embedding_cache.get(_embedding_key(chunk))
                if vector is None:
                    continue
                semantic = _cosine_similarity(query_vector, vector)
                lexical_score = lexical_by_id.get(chunk.id, 0.0)
                normalized_lexical = lexical_score / maximum_lexical
                score = 0.78 * semantic + 0.22 * normalized_lexical
                hybrid.append(SearchHit(
                    chunk=chunk,
                    score=score,
                    lexical_score=lexical_score,
                    semantic_score=semantic,
                ))
        return sorted(hybrid, key=lambda hit: (-hit.score, hit.chunk.title))[:limit]
    except Exception as exc:
        logger.warning("Semantic knowledge search failed; using lexical fallback: %s", exc)
        return lexical[:limit]


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
    semantic_confidence = hits[0].semantic_score is not None and hits[0].semantic_score >= 0.42
    return (
        normalized_query in normalized_title
        or normalized_title in normalized_query
        or overlap >= 0.30
        or semantic_confidence
    )


def _is_off_topic(question: str) -> bool:
    lowered = question.casefold()
    allowed = (
        "конкурс", "заяв", "музей", "грант", "бюджет", "партн", "проект", "документ",
        "участв", "команд", "аудитор", "социальн", "креатив", "фонд", "срок", "руковод",
        "продукт", "результат", "риск", "ресурс", "привет", "здравств",
    )
    return not any(token in lowered for token in allowed)


def _is_help_question(question: str) -> bool:
    normalized = " ".join(re.findall(r"[а-яёa-z0-9]+", question.casefold()))
    phrases = (
        "как ты работаешь", "как вы работаете", "как с тобой работать",
        "что ты умеешь", "что вы умеете", "чем ты можешь помочь",
        "чем вы можете помочь", "расскажи о себе",
    )
    return any(phrase in normalized for phrase in phrases)


def _retrieval_question(question: str, history: list[tuple[str, str]] | None) -> str:
    if not history:
        return question
    lowered = question.casefold().strip()
    tokens = WORD_RE.findall(lowered)
    is_follow_up = len(tokens) <= 7 and (
        lowered.startswith(("а ", "и ", "но ", "он ", "она ", "они ", "это "))
        or any(word in tokens for word in ("там", "тогда", "такой", "такая", "такие"))
    )
    if not is_follow_up:
        return question
    previous_user = next((text for role, text in reversed(history) if role == "user"), "")
    return f"{previous_user}\nУточнение: {question}" if previous_user else question


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


def _context_from_hits(qa_hits: list[SearchHit], general_hits: list[SearchHit]) -> tuple[str, list[SearchHit]]:
    parts: list[str] = []
    included: list[SearchHit] = []
    total_chars = 0
    for layer, hits in (("qa", qa_hits), ("general", general_hits)):
        for hit in hits:
            content = hit.chunk.content.strip()[:6000]
            part = (
                f"LAYER: {layer}\nSOURCE_ID: {hit.chunk.id}\nTITLE: {hit.chunk.title}\n"
                f"SECTION: {hit.chunk.section}\nCONTENT: {content}"
            )
            if total_chars + len(part) > 40000 and included:
                continue
            parts.append(part)
            included.append(hit)
            total_chars += len(part)
    return "\n\n".join(parts), included


def _history_text(history: list[tuple[str, str]] | None) -> str:
    if not history:
        return "Предыдущих сообщений нет."
    labels = {"user": "Пользователь", "assistant": "Помощник"}
    lines = [f"{labels.get(role, role)}: {text[:1600]}" for role, text in history[-8:]]
    return "\n".join(lines)[:8000]


def _record_local_answer(db: Session, request_id: str, purpose: str) -> None:
    db.add(ApiCall(
        request_id=request_id,
        purpose=purpose,
        model="local-retrieval",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0,
        cost_status="known",
        duration_ms=0,
    ))


def answer_question(
    db: Session,
    request_id: str,
    question: str,
    history: list[tuple[str, str]] | None = None,
) -> tuple[ModelAnswer, list[KnowledgeChunk], str]:
    version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "published"))
    version_id = version.id if version else ""

    if _is_help_question(question):
        result = ModelAnswer(status="answered", answer=HELP_ANSWER, source_ids=[])
        _record_local_answer(db, request_id, "chat-static-help")
        db.commit()
        return result, [], version_id

    retrieval_question = _retrieval_question(question, history)
    qa_hits = _hybrid_search(db, request_id, retrieval_question, limit=8, topic="qa")
    general_hits = _hybrid_search(db, request_id, retrieval_question, limit=6, topic="general")
    use_qa = _qa_match_confident(question, qa_hits)
    if settings.demo_mode or not settings.openai_api_key:
        result = _demo_answer(db, question, qa_hits, general_hits)
        all_hits = qa_hits + general_hits
        selected = [hit.chunk for hit in all_hits if hit.chunk.id in result.source_ids]
        _record_local_answer(db, request_id, "chat-demo-retrieval")
        db.commit()
        return result, selected, version_id

    context, hits = _context_from_hits(qa_hits, general_hits)
    if not hits:
        result = ModelAnswer(status="no_answer", answer=get_escalation_text(db), source_ids=[])
        _record_local_answer(db, request_id, "chat-empty-retrieval")
        db.commit()
        return result, [], version_id

    prompt = (
        "Ты официальный помощник конкурса «Креативный музей». Отвечай дружелюбно, профессионально, "
        "кратко и структурированно. Сначала обязательно проверь материалы LAYER: qa: это приоритетные "
        "готовые ответы. Считай вопрос совпавшим, даже если пользователь использовал другую форму слова, "
        "синоним или более короткую формулировку. Если Q&A прямо отвечает на вопрос, используй его и не "
        "возвращай no_answer. Затем при необходимости дополни ответ материалами LAYER: general. "
        "Отвечай только по переданным материалам конкурса; история диалога нужна лишь для понимания контекста "
        "и не является источником фактов. Не выдумывай правила, даты и требования, не давай экспертных оценок. "
        "Если подтверждённых данных действительно недостаточно, верни no_answer. Для неоднозначного вопроса "
        "верни clarification, для сторонней темы — off_topic. В source_ids укажи все использованные SOURCE_ID "
        "и только их. Старые даты явно помечай как относящиеся к сезону 2025–2026."
    )
    started = time.perf_counter()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.parse(
        model=settings.openai_model,
        reasoning={"effort": settings.openai_reasoning_effort},
        max_output_tokens=settings.openai_max_output_tokens,
        input=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"ИСТОРИЯ ДИАЛОГА:\n{_history_text(history)}\n\n"
                    f"ТЕКУЩИЙ ВОПРОС:\n{question}\n\nМАТЕРИАЛЫ:\n{context}"
                ),
            },
        ],
        text_format=ModelAnswer,
    )
    parsed = response.output_parsed
    if parsed.status in {"no_answer", "off_topic"}:
        parsed.answer = get_escalation_text(db)
    allowed_ids = {hit.chunk.id for hit in hits}
    parsed.source_ids = [source_id for source_id in parsed.source_ids if source_id in allowed_ids]
    if parsed.status == "answered" and not parsed.source_ids:
        fallback_hit = qa_hits[0] if use_qa and qa_hits else (general_hits[0] if general_hits else hits[0])
        parsed.source_ids = [fallback_hit.chunk.id]
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
