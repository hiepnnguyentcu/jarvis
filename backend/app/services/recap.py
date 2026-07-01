"""
Recap generation: 1-hop AGE traversal + Claude summary.
Returns a short spoken briefing the wearer hears before meeting someone again.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import cypher_multi
from app.models.graph import EntityEmbedding


async def build_recap(
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> str:
    """
    Build a 2-3 sentence spoken recap for the wearer about a known person.
    Falls back to a generic message when the graph has no data.
    """
    from app.models.person import Person
    from app.models.session import Session

    person = await db.get(Person, person_id)
    if not person:
        return "I don't have any information about this person yet."

    # Locate person's entity in the graph
    result = await db.execute(
        select(EntityEmbedding)
        .where(EntityEmbedding.user_id == user_id)
        .where(EntityEmbedding.entity_type == "person")
        .where(EntityEmbedding.canonical_name == person.name)
        .limit(1)
    )
    person_entity = result.scalar_one_or_none()

    # 1-hop graph traversal
    graph_facts: list[str] = []
    if person_entity:
        eid = str(person_entity.id)
        uid = str(user_id)
        try:
            rows = (await cypher_multi(
                db,
                f"MATCH (a:Entity {{entity_id: '{eid}', user_id: '{uid}'}})"
                f"-[r:SPO]->(b:Entity) "
                f"RETURN r.predicate, b.canonical_name",
                ["predicate", "to_name"],
            )).fetchall()
            for row in rows:
                pred = str(row[0]).strip('"').replace("_", " ")
                obj = str(row[1]).strip('"')
                graph_facts.append(f"{person.name} {pred} {obj}")
        except Exception:
            pass

    # Recent session dates for temporal context
    sess_result = await db.execute(
        select(Session)
        .where(Session.user_id == user_id)
        .where(Session.person_id == person_id)
        .where(Session.ended_at.is_not(None))
        .order_by(Session.started_at.desc())
        .limit(3)
    )
    recent_sessions = sess_result.scalars().all()

    # Detect whether this is the wearer's own profile
    from app.models.user import User
    user = await db.get(User, user_id)
    is_wearer = user is not None and user.wearer_person_id == person_id

    if not graph_facts and not recent_sessions:
        if is_wearer:
            return "I don't have much about you yet. Tell me more and I'll remember it."
        return (
            f"You've met {person.name} before but I don't have detailed notes yet. "
            "Have a great conversation!"
        )

    return await _generate_recap(person.name, graph_facts, recent_sessions, is_wearer=is_wearer)


async def _generate_recap(
    name: str,
    facts: list[str],
    sessions: list,
    is_wearer: bool = False,
) -> str:
    if settings.mock_graph_extraction or not settings.anthropic_api_key:
        return _mock_recap(name, facts, sessions, is_wearer)

    facts_text = "\n".join(f"- {f}" for f in facts[:15]) or "No specific facts recorded."
    session_dates = ", ".join(
        s.started_at.strftime("%B %d") for s in sessions
    ) if sessions else "unknown date"

    if is_wearer:
        prompt = (
            f"You are Jarvis, a personal AI memory assistant living in the wearer's smart glasses.\n"
            f"The following facts have been learned about the wearer ({name}) from recent conversations:\n"
            f"{facts_text}\n\n"
            f"Sessions recorded: {session_dates}\n\n"
            "Write a 2-3 sentence personal summary the wearer would hear when checking their own profile. "
            "Speak directly to the wearer as 'you'. Be warm and conversational, not robotic. "
            "Highlight interesting or recent things you know about them. "
            "Start with something like 'Here's what I know about you...' or 'Recently, you told me...' "
            "Keep it under 60 words."
        )
    else:
        prompt = (
            f"You are Jarvis, a discrete AI assistant in the wearer's smart glasses.\n"
            f"The wearer is about to talk to {name} again.\n\n"
            f"Known facts about {name} from previous conversations:\n{facts_text}\n\n"
            f"Previous conversations: {session_dates}\n\n"
            f"Write a 2-3 sentence spoken briefing the wearer will hear through their glasses "
            "before the conversation starts. Be conversational, not listy. Mention only the "
            "most recent or surprising facts. Start with 'Last time you spoke...' or similar. "
            "Keep it under 50 words."
        )

    try:
        import anthropic  # type: ignore[import]
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return _mock_recap(name, facts, sessions)


def _mock_recap(name: str, facts: list[str], sessions: list, is_wearer: bool = False) -> str:
    if is_wearer:
        if not facts:
            return "I don't have much about you yet. Tell me more and I'll remember it."
        top = facts[0]
        extra = f" You also {facts[1]}." if len(facts) > 1 else ""
        return f"Here's what I know about you: {top}.{extra}"
    if not facts:
        return (
            f"Last time you spoke with {name}, you had a great conversation. "
            "Good luck today!"
        )
    top = facts[0]
    extra = f" Also, {facts[1]}." if len(facts) > 1 else ""
    return f"Last time you spoke with {name}, you learned that {top}.{extra}"
