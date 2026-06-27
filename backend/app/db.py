from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    # AGE requires ag_catalog in the search_path for Cypher functions.
    # shared_preload_libraries='age' in postgresql.conf handles LOAD automatically.
    connect_args={
        "server_settings": {
            "search_path": "ag_catalog, \"$user\", public",
        }
    },
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def cypher(session: AsyncSession, query: str) -> Any:
    """
    Execute a Cypher query against the jarvis_kg AGE graph.

    Uses exec_driver_sql to bypass SQLAlchemy's :param substitution, which
    would otherwise misinterpret Cypher label syntax like (:Person) as bind params.

    Returns a CursorResult; call .fetchall() or iterate for rows.
    Each row has one 'result agtype' column per RETURN expression.
    agtype values come back as JSON strings — parse with json.loads().

    Example:
        rows = (await cypher(session, "MATCH (p:Person {person_id: 'x'}) RETURN p")).fetchall()
    """
    conn = await session.connection()
    return await conn.exec_driver_sql(
        f"SELECT * FROM cypher('jarvis_kg', $$ {query} $$) AS (result agtype)"
    )


async def cypher_multi(session: AsyncSession, query: str, columns: list[str]) -> Any:
    """
    Cypher query that returns multiple columns.

    columns: list of column names matching the RETURN clause order.
    Each column is returned as agtype (JSON string).

    Example:
        rows = (await cypher_multi(
            session,
            "MATCH (p:Person)-[r]->(n) RETURN type(r), n.canonical_name",
            ["predicate", "name"]
        )).fetchall()
    """
    col_defs = ", ".join(f"{c} agtype" for c in columns)
    conn = await session.connection()
    return await conn.exec_driver_sql(
        f"SELECT * FROM cypher('jarvis_kg', $$ {query} $$) AS ({col_defs})"
    )
