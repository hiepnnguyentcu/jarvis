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
    subject_type: str
    subject_icon: str = "🔹"
    predicate: str
    object: str
    object_type: str
    object_icon: str = "🔹"
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
        f"You are building a multi-hop knowledge graph from a conversation snippet.\n"
        f"Speaker: {speaker_name}\n"
        f'Text: "{text}"\n'
        f"Known entities for context: {ctx}\n\n"
        "Extract EVERY factual relationship as a subject-predicate-object triple.\n"
        "This includes TWO kinds of triples — extract both:\n"
        f"  1. Speaker-to-entity: facts about {speaker_name} (e.g. 'I work at Google' → ({speaker_name}, works_at, Google))\n"
        "  2. Entity-to-entity: facts between non-speaker entities mentioned in the text\n"
        "     e.g. 'I study at TCU in Fort Worth' → ALSO extract (TCU, located_in, Fort Worth)\n"
        "     e.g. 'I love hiking, especially in national parks' → (hiking, takes_place_in, national parks)\n"
        "     e.g. 'I have two dogs, they are golden retrievers' → (2 dogs, is_breed, golden retriever)\n\n"
        "Rules:\n"
        "- Subject can be ANY named entity — it does NOT have to be the speaker\n"
        "- Predicate: short snake_case verb phrase (works_at, lives_in, located_in, is_a, is_type,\n"
        "  knows, attended, studied_at, is_interested_in, used_to_work_at, is_friends_with,\n"
        "  is_part_of, founded, is_building, takes_place_in, is_breed, is_located_in, etc.)\n"
        "- Object is a named entity or specific value\n"
        "- Only extract explicit statements — not implications or guesses\n"
        "- Normalize names to canonical form (e.g. 'TCU' → 'Texas Christian University')\n"
        "- confidence: 1.0=stated directly, 0.7=paraphrase, 0.5=implied\n"
        "- subject_type/object_type — pick the most specific that fits:\n"
        "    person     → individual human (Hiep, Professor Kim, Dario Amodei)\n"
        "    company    → organisation, university, lab, institution (Google, TCU, OpenAI)\n"
        "    place      → physical location (Fort Worth, California, Palo Duro Canyon)\n"
        "    product    → software, hardware, app, device (ChatGPT, Jarvis, Meta Ray-Ban)\n"
        "    role       → job title or academic role (research engineer, grad student, advisor)\n"
        "    field      → academic discipline or profession (software engineering, AI research)\n"
        "    activity   → hobby, sport, or recurring action (hiking, cooking, football)\n"
        "    topic      → abstract concept or area of interest (AI safety, machine learning)\n"
        "    event      → one-time occurrence (conference, graduation)\n"
        "    animal     → pet or animal (dog, cat, golden retriever)\n"
        "    misc       → only if nothing above fits\n"
        "- subject_icon/object_icon: a single emoji that best represents THIS SPECIFIC entity.\n"
        "  Go beyond the type — pick something visually distinctive for the entity itself.\n"
        "  Examples: TCU→🏛️, OpenAI→🤖, hiking→🥾, Palo Duro Canyon→🏜️, PhD→🎓,\n"
        "  Anthropic→🛡️, ChatGPT→💬, dog→🐕, cooking→🍳, football→🏈, glasses→👓\n\n"
        'Return JSON only: {"triples": [...]}\n'
        "Each triple: {\"subject\": str, \"subject_type\": str, \"subject_icon\": str, "
        "\"predicate\": str, \"object\": str, \"object_type\": str, \"object_icon\": str, "
        "\"object_properties\": dict, \"confidence\": float}\n"
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
    icon: str = "🔹",
) -> uuid.UUID:
    """
    Find or create an entity by canonical_name. Matches on name alone so the same
    real-world entity never gets two nodes due to a type disagreement between Claude calls.
    """
    result = await db.execute(
        select(EntityEmbedding)
        .where(EntityEmbedding.user_id == user_id)
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
    icon_safe = icon.replace("'", "\\'")
    await cypher(
        db,
        f"CREATE (:Entity {{entity_id: '{eid}', canonical_name: '{name_safe}', "
        f"entity_type: '{type_safe}', icon: '{icon_safe}', user_id: '{uid}'}})",
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

    async def get_entity(name: str, etype: str, props: dict, icon: str) -> uuid.UUID:
        key = name.lower()
        if key not in seen:
            seen[key] = await resolve_entity(user_id, name, etype, props, db, icon)
        return seen[key]

    sess_str = str(session_id)
    for triple in triples[:settings.fact_max_per_session]:
        from_id = await get_entity(triple.subject, triple.subject_type, {}, triple.subject_icon)
        to_id = await get_entity(triple.object, triple.object_type, triple.object_properties, triple.object_icon)

        fid, tid = str(from_id), str(to_id)
        pred = triple.predicate.replace("'", "\\'")
        conf = triple.confidence

        # Dedup: skip if ANY edge already exists between these two nodes
        existing = (await cypher_multi(
            db,
            f"MATCH (a:Entity {{entity_id: '{fid}'}})"
            f"-[r:SPO]->"
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


_MIN_WORDS = 8
_VAGUE_OBJECTS = {"unknown", "something", "something (unspecified)", "advisor", "unspecified", ""}


def _is_useful(triple: Triple) -> bool:
    """Filter out junk triples before storing."""
    subj = triple.subject.strip().lower()
    obj  = triple.object.strip().lower()
    # Self-referential
    if subj == obj:
        return False
    # Vague / placeholder objects
    if obj in _VAGUE_OBJECTS or len(obj) < 2:
        return False
    # Very low confidence
    if triple.confidence < 0.55:
        return False
    # Redundant identity facts
    if triple.predicate in {"has_name", "is_called", "is_named"}:
        return False
    return True


async def extract_and_store_for_session(
    session_id: uuid.UUID,
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
    speaker_role_filter: str = "other",
) -> int:
    """
    Run graph extraction for segments matching speaker_role_filter.
    Returns total number of triples stored.
    """
    from app.models.person import Person
    from app.models.session import Segment

    person = await db.get(Person, person_id)
    if not person:
        return 0

    await resolve_entity(user_id, person.name, "person", {}, db)
    await db.commit()

    result = await db.execute(
        select(Segment)
        .where(Segment.session_id == session_id)
        .where(Segment.speaker_role == speaker_role_filter)
        .order_by(Segment.start_ms)
    )
    segments = result.scalars().all()

    total = 0
    existing_context: list[str] = []
    pending = ""

    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue

        # Accumulate short/partial segments into the next one
        words = text.split()
        if len(words) < _MIN_WORDS:
            pending = (pending + " " + text).strip()
            continue

        full_text = (pending + " " + text).strip() if pending else text
        pending = ""

        triples = await extract_triples(full_text, person.name, existing_context)
        useful  = [t for t in triples if _is_useful(t)]
        if useful:
            await store_triples(session_id, user_id, useful, db)
            total += len(useful)
            existing_context = list({
                *existing_context,
                *(t.subject for t in useful),
                *(t.object for t in useful),
            })[:20]

    # Flush any leftover pending text
    if pending and len(pending.split()) >= 4:
        triples = await extract_triples(pending, person.name, existing_context)
        useful  = [t for t in triples if _is_useful(t)]
        if useful:
            await store_triples(session_id, user_id, useful, db)
            total += len(useful)

    return total


async def get_person_graph(
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
    max_hops: int = 4,
) -> dict:
    """
    Return the full entity graph reachable from a person node up to max_hops deep.
    Performs BFS hop-by-hop so depth is bounded and cycles are handled.
    """
    from app.models.person import Person

    person = await db.get(Person, person_id)
    if not person:
        return {"person_id": str(person_id), "nodes": [], "edges": []}

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

    root_eid = str(person_entity.id)
    uid = str(user_id)

    # Fetch root node icon from AGE
    try:
        root_row = (await cypher_multi(
            db,
            f"MATCH (e:Entity {{entity_id: '{root_eid}'}}) RETURN e.icon",
            ["icon"],
        )).fetchone()
        root_icon = str(root_row[0]).strip('"') if root_row and root_row[0] else "👤"
    except Exception:
        root_icon = "👤"

    nodes: list[dict] = [{"id": root_eid, "name": person.name, "type": "person", "icon": root_icon, "depth": 0}]
    nodes_seen: set[str] = {root_eid}
    edges: list[dict] = []

    frontier: list[str] = [root_eid]

    for _hop in range(max_hops):
        if not frontier:
            break

        id_list = ", ".join(f"'{fid}'" for fid in frontier)
        try:
            rows = (await cypher_multi(
                db,
                f"MATCH (a:Entity)-[r:SPO]->(b:Entity) "
                f"WHERE a.entity_id IN [{id_list}] AND a.user_id = '{uid}' "
                f"RETURN a.entity_id, r.predicate, b.canonical_name, b.entity_type, b.entity_id, r.confidence, b.icon",
                ["from_eid", "predicate", "to_name", "to_type", "to_eid", "confidence", "to_icon"],
            )).fetchall()
        except Exception:
            rows = []

        next_frontier: list[str] = []
        for row in rows:
            from_eid = str(row[0]).strip('"')
            pred     = str(row[1]).strip('"')
            to_name  = str(row[2]).strip('"')
            to_type  = str(row[3]).strip('"')
            to_eid   = str(row[4]).strip('"')
            to_icon  = str(row[6]).strip('"') if row[6] and str(row[6]) != "null" else "🔹"
            try:
                conf = float(str(row[5]).strip('"'))
            except (ValueError, TypeError):
                conf = 0.0

            if to_eid not in nodes_seen:
                nodes.append({"id": to_eid, "name": to_name, "type": to_type, "icon": to_icon, "depth": _hop + 1})
                nodes_seen.add(to_eid)
                next_frontier.append(to_eid)

            edges.append({"from": from_eid, "predicate": pred, "to": to_eid, "confidence": conf})

        frontier = next_frontier

    return {
        "person_id": str(person_id),
        "name": person.name,
        "nodes": nodes,
        "edges": edges,
        "max_depth": max(n["depth"] for n in nodes) if nodes else 0,
    }
