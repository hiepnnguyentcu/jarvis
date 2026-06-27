"""
Knowledge graph extraction: SPO triple extraction + entity resolution.

MOCK_GRAPH_EXTRACTION=true → heuristic extraction (no API key needed).
When anthropic_api_key is empty, mock mode is forced automatically.

Entity dedup uses exact canonical_name match — Claude normalizes names during
extraction (e.g. always "Google" not "Google Inc"), so exact match is sufficient.
"""
import json
import uuid
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import cypher, cypher_multi
from app.models.graph import EntityEmbedding


class Triple(BaseModel):
    subject: str
    subject_type: str  # person | company | place | topic | event | misc
    predicate: str
    object: str
    object_type: str
    object_properties: dict = {}
    confidence: float


def _extract_mock(text: str, speaker_name: str) -> list[Triple]:
    """
    Heuristic extraction for dev/test — no Claude call. Scans for known keywords
    from the sample_diarized.json fixture so tests get realistic triples.
    """
    triples: list[Triple] = []
    locations = {"Tokyo": "place", "San Francisco": "place", "New York": "place"}

    for loc, loc_type in locations.items():
        if loc not in text:
            continue
        text_lower = text.lower()
        if any(kw in text_lower for kw in ("move", "moving", "relocat")):
            triples.append(Triple(
                subject=speaker_name, subject_type="person",
                predicate="is_planning_to_move_to",
                object=loc, object_type=loc_type,
                object_properties={}, confidence=0.85,
            ))
        elif any(kw in text_lower for kw in ("trip", "back from", "went", "visit")):
            triples.append(Triple(
                subject=speaker_name, subject_type="person",
                predicate="visited",
                object=loc, object_type=loc_type,
                object_properties={}, confidence=0.90,
            ))
        else:
            triples.append(Triple(
                subject=speaker_name, subject_type="person",
                predicate="mentioned",
                object=loc, object_type=loc_type,
                object_properties={}, confidence=0.70,
            ))

    if any(kw in text.lower() for kw in ("office", "company", "opening a")):
        triples.append(Triple(
            subject=speaker_name, subject_type="person",
            predicate="works_at",
            object=f"{speaker_name}_company", object_type="company",
            object_properties={}, confidence=0.75,
        ))

    return triples


async def extract_triples(
    text: str,
    speaker_name: str,
    existing_context: Optional[list[str]] = None,
) -> list[Triple]:
    """
    Extract SPO triples from an utterance.
    Falls back to heuristic mock when no Anthropic key or MOCK_GRAPH_EXTRACTION=true.
    """
    if settings.mock_graph_extraction or not settings.anthropic_api_key:
        return _extract_mock(text, speaker_name)

    ctx = ", ".join(existing_context or []) or "none"
    prompt = (
        f"You are extracting a knowledge graph from a conversation snippet.\n"
        f"Speaker: {speaker_name}\n"
        f'Text: "{text}"\n'
        f"Known entities for context: {ctx}\n\n"
        "Extract every factual relationship as a subject-predicate-object triple.\n"
        "Rules:\n"
        "- Subject is always a named entity (person, company, place, topic, or event)\n"
        "- Predicate is a short snake_case verb phrase: works_at, lives_in, knows, attended,\n"
        "  is_building, studied_at, is_interested_in, used_to_work_at, is_friends_with,\n"
        "  is_planning_to_move_to, visited, etc.\n"
        "- Object is a named entity or a specific value\n"
        "- Only extract explicit statements, not implications\n"
        "- Normalize names to canonical form (e.g. 'Google' not 'Google Inc')\n"
        "- confidence: 1.0=stated directly, 0.7=paraphrase, 0.5=implied\n"
        "- subject_type/object_type: person | company | place | topic | event | misc\n\n"
        'Return JSON only: {"triples": [...]}\n'
        "Each triple: {\"subject\": str, \"subject_type\": str, \"predicate\": str, "
        "\"object\": str, \"object_type\": str, \"object_properties\": dict, \"confidence\": float}\n"
        'If no facts found, return {"triples": []}'
    )

    try:
        import anthropic as _anthropic  # type: ignore[import]
        client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        data = json.loads(raw)
        return [Triple(**t) for t in data.get("triples", [])]
    except Exception:
        return _extract_mock(text, speaker_name)


async def resolve_entity(
    user_id: uuid.UUID,
    canonical_name: str,
    entity_type: str,
    properties: dict,
    db: AsyncSession,
) -> uuid.UUID:
    """
    Find or create an entity by exact canonical_name match.
    Claude normalizes names during extraction so exact match is sufficient.
    Returns entity_embedding.id which is also stored as entity_id on the AGE vertex.
    """
    result = await db.execute(
        select(EntityEmbedding)
        .where(EntityEmbedding.user_id == user_id)
        .where(EntityEmbedding.entity_type == entity_type)
        .where(EntityEmbedding.canonical_name == canonical_name)
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing.id

    entity_id = uuid.uuid4()
    db.add(EntityEmbedding(
        id=entity_id,
        user_id=user_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
    ))
    await db.flush()

    eid = str(entity_id)
    uid = str(user_id)
    name_safe = canonical_name.replace("'", "\\'")
    type_safe = entity_type.replace("'", "\\'")
    await cypher(
        db,
        f"CREATE (:Entity {{entity_id: '{eid}', canonical_name: '{name_safe}', "
        f"entity_type: '{type_safe}', user_id: '{uid}'}})",
    )
    return entity_id


async def store_triples(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    triples: list[Triple],
    db: AsyncSession,
) -> None:
    """Resolve entities and write SPO edges into the AGE jarvis_kg graph."""
    seen: dict[str, uuid.UUID] = {}

    async def get_entity(name: str, etype: str, props: dict) -> uuid.UUID:
        key = f"{etype}:{name.lower()}"
        if key not in seen:
            seen[key] = await resolve_entity(user_id, name, etype, props, db)
        return seen[key]

    sess_str = str(session_id)
    for triple in triples[:settings.fact_max_per_session]:
        from_id = await get_entity(triple.subject, triple.subject_type, {})
        to_id = await get_entity(triple.object, triple.object_type, triple.object_properties)

        fid, tid = str(from_id), str(to_id)
        pred = triple.predicate.replace("'", "\\'")
        conf = triple.confidence

        # Check for existing edge to avoid duplicates on re-extraction
        existing = (await cypher_multi(
            db,
            f"MATCH (a:Entity {{entity_id: '{fid}'}})"
            f"-[r:SPO {{predicate: '{pred}'}}]->"
            f"(b:Entity {{entity_id: '{tid}'}}) RETURN r",
            ["r"],
        )).fetchone()

        if not existing:
            await cypher(
                db,
                f"MATCH (a:Entity {{entity_id: '{fid}'}}), (b:Entity {{entity_id: '{tid}'}}) "
                f"CREATE (a)-[:SPO {{predicate: '{pred}', confidence: {conf}, "
                f"session_id: '{sess_str}'}}]->(b)",
            )

    await db.commit()


async def extract_and_store_for_session(
    session_id: uuid.UUID,
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> int:
    """
    Run graph extraction for all segments in a session.
    Returns total number of triples stored.
    """
    from app.models.person import Person
    from app.models.session import Segment

    person = await db.get(Person, person_id)
    if not person:
        return 0

    # Ensure the person has an Entity node in the graph
    await resolve_entity(user_id, person.name, "person", {}, db)
    await db.commit()

    result = await db.execute(
        select(Segment).where(Segment.session_id == session_id)
    )
    segments = result.scalars().all()

    total = 0
    existing_context: list[str] = []

    for seg in segments:
        # Attribute the utterance to the person when they're the other speaker
        if seg.speaker_role == "other" or seg.speaker_label == "B":
            speaker_name = person.name
        else:
            speaker_name = "Wearer"

        triples = await extract_triples(seg.text, speaker_name, existing_context)
        if triples:
            await store_triples(session_id, user_id, triples, db)
            total += len(triples)
            existing_context = list({
                *existing_context,
                *(t.subject for t in triples),
                *(t.object for t in triples),
            })[:20]

    return total


async def get_person_graph(
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Return entity node + 1-hop relationships for a person."""
    from app.models.person import Person

    person = await db.get(Person, person_id)
    if not person:
        return {"person_id": str(person_id), "nodes": [], "edges": []}

    # Locate person's entity node by canonical_name + type
    result = await db.execute(
        select(EntityEmbedding)
        .where(EntityEmbedding.user_id == user_id)
        .where(EntityEmbedding.entity_type == "person")
        .where(EntityEmbedding.canonical_name == person.name)
        .limit(1)
    )
    person_entity = result.scalar_one_or_none()
    if not person_entity:
        return {"person_id": str(person_id), "name": person.name, "nodes": [], "edges": []}

    eid = str(person_entity.id)
    uid = str(user_id)

    try:
        rows = (await cypher_multi(
            db,
            f"MATCH (a:Entity {{entity_id: '{eid}', user_id: '{uid}'}})"
            f"-[r:SPO]->(b:Entity) "
            f"RETURN r.predicate, b.canonical_name, b.entity_type, b.entity_id, r.confidence",
            ["predicate", "to_name", "to_type", "to_entity_id", "confidence"],
        )).fetchall()
    except Exception:
        rows = []

    nodes = [{"id": eid, "name": person.name, "type": "person"}]
    nodes_seen: set[str] = {eid}
    edges: list[dict] = []

    for row in rows:
        pred = str(row[0]).strip('"')
        to_name = str(row[1]).strip('"')
        to_type = str(row[2]).strip('"')
        to_eid = str(row[3]).strip('"')
        try:
            conf = float(str(row[4]).strip('"'))
        except (ValueError, TypeError):
            conf = 0.0

        if to_eid not in nodes_seen:
            nodes.append({"id": to_eid, "name": to_name, "type": to_type})
            nodes_seen.add(to_eid)

        edges.append({"from": eid, "predicate": pred, "to": to_eid, "confidence": conf})

    return {
        "person_id": str(person_id),
        "name": person.name,
        "nodes": nodes,
        "edges": edges,
    }
