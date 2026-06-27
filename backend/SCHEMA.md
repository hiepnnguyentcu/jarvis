# Jarvis — Database Schema

Jarvis uses two storage layers that work together:

1. **PostgreSQL 16 (SQL)** — auth, people, sessions, transcripts, voice embeddings. Managed by Alembic + SQLModel.
2. **Apache AGE (graph)** — the knowledge graph. A property graph stored natively in Postgres via the AGE extension. Queried with Cypher.
3. **pgvector bridge** — `entity_embedding` table links AGE vertices to 1536-d text embeddings for entity resolution (dedup before inserting a new graph node).

All SQL PKs are UUIDs. All timestamps are UTC `TIMESTAMP WITHOUT TIME ZONE`.

---

## SQL Tables

### `user`
Jarvis account. One per person using the app.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `email` | `text` | NO | unique, indexed |
| `hashed_password` | `text` | NO | bcrypt (pinned bcrypt==4.0.1) |
| `voice_enrolled` | `boolean` | NO | default `false`; set `true` after `POST /auth/enroll-voice` |
| `created_at` | `timestamp` | NO | |

---

### `uservoiceembedding`
The wearer's own voice embedding, enrolled at onboarding. Used to anchor speaker role resolution — all other speakers are classified by comparing against this first.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `user_id` | `uuid` | NO | FK → `user.id`, indexed |
| `embedding` | `vector(256)` | YES | resemblyzer embedding; null until Chunk 4 computes it |
| `created_at` | `timestamp` | NO | |

---

### `person`
A named individual the wearer has had a conversation with, identified by voice.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `user_id` | `uuid` | NO | FK → `user.id`, indexed |
| `name` | `text` | NO | provided by wearer at enrollment |
| `created_at` | `timestamp` | NO | |

When a person is enrolled, a corresponding AGE vertex `(:Person {person_id, name, user_id})` is created in `jarvis_kg`. The vertex is looked up by `person_id` property for graph writes.

---

### `voiceembedding`
Voice embedding for a known person. Used to identify them in future sessions via cosine similarity.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `person_id` | `uuid` | NO | FK → `person.id`, indexed |
| `embedding` | `vector(256)` | YES | resemblyzer embedding |
| `created_at` | `timestamp` | NO | |

Match query: `ORDER BY embedding <=> $1 LIMIT 1` + threshold check (≥ 0.85 → match).

---

### `session`
One conversation the wearer had. Created when VAD detects speech, closed on 10s silence.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `user_id` | `uuid` | NO | FK → `user.id`, indexed |
| `person_id` | `uuid` | YES | FK → `person.id`; set after identity match |
| `started_at` | `timestamp` | NO | |
| `ended_at` | `timestamp` | YES | null while active |
| `raw_transcript` | `text` | YES | full combined transcript, written at session end |
| `audio_r2_key` | `text` | YES | R2 object path for raw PCM audio |
| `identity_confidence` | `float` | YES | cosine similarity of the voice match (0–1) |

---

### `segment`
One finalized utterance from AssemblyAI. Written to DB in real-time.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `session_id` | `uuid` | NO | FK → `session.id`, indexed |
| `speaker_label` | `text` | NO | raw AssemblyAI label e.g. `"SPEAKER_A"` |
| `speaker_role` | `text` | YES | `'wearer'` \| `'other'` — resolved by identity service |
| `text` | `text` | NO | transcribed utterance text |
| `start_ms` | `integer` | NO | offset in ms from session start |
| `end_ms` | `integer` | NO | |

`speaker_role` is null until the identity service resolves it (requires ≥ 8s of per-speaker audio).

---

### `entity_embedding`
pgvector bridge table. One row per unique entity node in the AGE knowledge graph.

Used exclusively for **entity resolution**: before inserting a new entity extracted from conversation, embed its `canonical_name` and run a cosine similarity search here. If similarity > 0.92 with an existing row of the same `entity_type`, the entity already exists — MERGE into the existing AGE vertex rather than creating a duplicate.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NO | PK |
| `user_id` | `uuid` | NO | FK → `user.id`, indexed |
| `entity_type` | `text` | NO | `person` \| `company` \| `place` \| `topic` \| `event` \| `misc` |
| `canonical_name` | `text` | NO | indexed; matches the `canonical_name` property on the AGE vertex |
| `embedding` | `vector(1536)` | YES | `text-embedding-3-small` embedding of `canonical_name` |
| `created_at` | `timestamp` | NO | |

Dedup query: `ORDER BY embedding <=> $1 LIMIT 1` filtered by `entity_type` + threshold check.

---

## Knowledge Graph (Apache AGE)

Graph name: `jarvis_kg`. Lives inside the same Postgres instance as the SQL tables.

### Vertex Labels

| Label | Properties | Notes |
|---|---|---|
| `:Person` | `person_id` (uuid), `name`, `user_id` | One per enrolled person; `person_id` matches SQL `person.id` |
| `:Company` | `canonical_name`, `user_id`, `properties` (jsonb) | e.g. Google, OpenAI |
| `:Place` | `canonical_name`, `user_id`, `properties` | e.g. Tokyo, San Francisco |
| `:Topic` | `canonical_name`, `user_id` | e.g. "machine learning", "AR hardware" |
| `:Event` | `canonical_name`, `user_id`, `properties` | e.g. "NeurIPS 2024", "YC S24" |
| `:Misc` | `canonical_name`, `user_id` | anything that doesn't fit above |

All vertices include `user_id` for data isolation (multi-tenant graph).

### Edge Labels (Predicates)

Predicates are free-text snake_case strings. Common vocabulary:

| Predicate | Example |
|---|---|
| `WORKS_AT` | `(Alice)-[:WORKS_AT]->(Google)` |
| `LIVES_IN` | `(Alice)-[:LIVES_IN]->(Tokyo)` |
| `KNOWS` | `(Alice)-[:KNOWS]->(Bob)` |
| `ATTENDED` | `(Alice)-[:ATTENDED]->(NeurIPS 2024)` |
| `IS_BUILDING` | `(Alice)-[:IS_BUILDING]->(AR headset startup)` |
| `STUDIED_AT` | `(Alice)-[:STUDIED_AT]->(Stanford)` |
| `IS_INTERESTED_IN` | `(Alice)-[:IS_INTERESTED_IN]->(spatial computing)` |
| `USED_TO_WORK_AT` | `(Alice)-[:USED_TO_WORK_AT]->(Meta)` |
| `INVESTED_IN` | `(Alice)-[:INVESTED_IN]->(Waymo)` |
| `FOUNDED` | `(Alice)-[:FOUNDED]->(Acme Inc)` |

All edges include `confidence` (float 0–1), `source_session_id` (uuid), and `created_at` (timestamp) as properties.

### Example Cypher Queries

```cypher
-- 1-hop: everything known about Alice
MATCH (p:Person {person_id: 'uuid'})-[r]->(n)
RETURN type(r) AS predicate, n.canonical_name AS object, n.properties, r.created_at

-- 2-hop: Alice's extended context
MATCH (p:Person {person_id: 'uuid'})-[r1]->(n1)-[r2]->(n2)
RETURN type(r1), n1.canonical_name, type(r2), n2.canonical_name

-- Insert a new relationship from an extracted triple
MATCH (p:Person {person_id: $pid})
MERGE (c:Company {canonical_name: 'Google', user_id: $uid})
CREATE (p)-[:WORKS_AT {confidence: 0.95, source_session_id: $sid, created_at: $ts}]->(c)
```

---

## SQL Relationships

```
user ──< uservoiceembedding
user ──< person ──< voiceembedding
user ──< session ──< segment
person ──< session (person_id)
user ──< entity_embedding          ← pgvector bridge to AGE
```

```
AGE jarvis_kg:
(:Person {person_id}) ──[edge]──> (:Company | :Place | :Topic | :Event | :Misc)
```

The bridge: `entity_embedding.canonical_name` ↔ `AGE vertex.canonical_name`

---

## pgvector Notes

- Extension: `CREATE EXTENSION IF NOT EXISTS vector` (initial migration)
- Voice embeddings: `vector(256)` — resemblyzer output dimension
- Entity embeddings: `vector(1536)` — `text-embedding-3-small` output dimension
- Cosine distance operator: `<=>` (lower = more similar; 0 = identical)
- Dedup threshold: cosine similarity > 0.92 → same entity, merge instead of insert

## AGE Notes

- Extension: `CREATE EXTENSION IF NOT EXISTS age CASCADE` (second migration)
- Graph: `SELECT create_graph('jarvis_kg')`
- `shared_preload_libraries = 'age'` set in `postgresql.conf` via Docker image — no `LOAD 'age'` needed per session
- `search_path = ag_catalog, "$user", public` set via asyncpg `server_settings` in `db.py`
- Cypher runs via: `SELECT * FROM cypher('jarvis_kg', $$ ... $$) AS (col agtype)`
- agtype columns are returned as JSON strings; parse with `json.loads()`

---

## Models

| Table | SQLModel class | File |
|---|---|---|
| `user` | `User` | `app/models/user.py` |
| `uservoiceembedding` | `UserVoiceEmbedding` | `app/models/user.py` |
| `person` | `Person` | `app/models/person.py` |
| `voiceembedding` | `VoiceEmbedding` | `app/models/person.py` |
| `session` | `Session` | `app/models/session.py` |
| `segment` | `Segment` | `app/models/session.py` |
| `entity_embedding` | `EntityEmbedding` | `app/models/graph.py` |

AGE graph vertices/edges are not SQLModel classes — they are managed via raw Cypher in `app/services/graph.py` (Chunk 5).

Migrations: `alembic/versions/`. Run `alembic upgrade head` to apply all.
