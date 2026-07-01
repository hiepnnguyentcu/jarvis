# Jarvis

Wearable AI memory assistant for smart glasses. Listens to conversations, identifies speakers by voice, builds a per-person knowledge graph, and plays a "since last time" recap when you meet someone again.

## How it works

```
Glasses mic → Bluetooth (Meta DAT SDK) → iPhone → WebSocket → Backend
           ← Glasses speakers          ←                    ← recap audio
```

The glasses are just a mic and speaker. The iPhone relays audio to the backend over a WebSocket. All intelligence lives on the backend: transcription, voice identity, graph extraction, and recap generation.

When you meet someone the system knows, Jarvis plays a spoken briefing through your glasses before the conversation starts — not a flat fact list, but a coherent 2–3 sentence summary drawn from a multi-hop knowledge graph.

## Architecture

```
backend/          FastAPI — auth, sessions, graph extraction, recap
  app/
    routes/       auth.py · people.py · sessions.py
    services/     transcription.py · identity.py · graph_extraction.py · recap.py
    ws/           stream.py (WebSocket — PCM in, JSON segments out)
    models/       user · person · session · segment · graph
  alembic/        migrations

mobile/ios/       Native Swift app (iPhone relay)
  Services/       AuthService · AudioBridgeService · SessionService · PeopleService
  Views/          HomeView · PeopleListView · PersonDetailView · GraphCanvasView

infra/
  docker-compose.yml    Postgres + AGE · Redis · backend · Caddy · pgAdmin
```

**Data stores:**
- PostgreSQL 16 + pgvector — users, people, sessions, segments, entity embeddings
- Apache AGE (graph extension) — entity nodes and SPO edges
- Redis — session state, refresh token store
- Cloudflare R2 — raw audio files

## Key pipeline

1. **Transcription** — AssemblyAI Streaming v3, `speaker_labels=true`, produces diarized segments in real time
2. **Voice identity** — resemblyzer GE2E 256d embeddings; wearer enrolled at onboarding, contacts enrolled on first encounter; 8s minimum audio gate before match attempt
3. **Graph extraction** — Claude (`claude-sonnet-4-6`) extracts SPO triples from every finalized segment, including entity-to-entity facts (e.g. `TCU → located_in → Fort Worth`); BFS traversal up to 4 hops; AI-chosen emoji icon per entity
4. **Recap** — Claude generates a 2–3 sentence spoken briefing from the graph; wearer-aware prompt when viewing own profile

## Local setup

**Prerequisites:** Docker, an AssemblyAI key, an Anthropic key.

```bash
# 1. Copy env template and fill in keys
cp backend/.env.example backend/.env

# 2. Start all services
cd infra && docker compose up -d

# 3. Run migrations
docker compose exec backend alembic upgrade head

# 4. Open the iOS app in Xcode, set the backend URL, run on device
```

**Dev mode without API keys** — set these in `backend/.env` to skip external calls:
```
MOCK_ASSEMBLYAI=true
MOCK_GRAPH_EXTRACTION=true
```

## End-to-end test (backend only)

```bash
docker compose exec backend \
  -e JARVIS_EMAIL=you@example.com \
  -e JARVIS_PASSWORD=yourpassword \
  python /app/scripts/stream_session.py
```

Streams a local M4A file over the WebSocket at realtime pace, runs extraction, and prints the recap.

## Key config (`backend/.env`)

| Variable | Description |
|---|---|
| `ASSEMBLYAI_API_KEY` | AssemblyAI streaming key |
| `ANTHROPIC_API_KEY` | Claude API key (graph extraction + recap) |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `R2_ACCESS_KEY` / `R2_SECRET_KEY` / `R2_BUCKET` / `R2_ENDPOINT_URL` | Cloudflare R2 for audio storage |
| `WEARER_MATCH_THRESHOLD` | Cosine similarity threshold for wearer identity (default 0.78) |
| `MOCK_ASSEMBLYAI` | Skip AssemblyAI, replay fixture segments |
| `MOCK_GRAPH_EXTRACTION` | Skip Claude, use heuristic extraction |

## API overview

```
POST /auth/register
POST /auth/login
POST /auth/refresh
GET  /auth/me
POST /auth/enroll-voice          # wearer voice enrollment

GET  /people
POST /people
GET  /people/{id}
POST /people/{id}/enroll         # contact voice enrollment
GET  /people/{id}/graph          # entity graph (nodes + edges, up to 4 hops)
GET  /people/{id}/recap          # generate spoken recap

POST /sessions
POST /sessions/{id}/end
POST /sessions/{id}/extract      # trigger graph extraction manually

WS   /ws/stream/{session_id}     # binary PCM in, JSON segments out
```

## iOS app

The native Swift app is the phone relay. It handles auth, voice enrollment, live session streaming, and surfaces the knowledge graph.

- **HomeView** — start/stop session, live speaker log
- **PeopleListView** — all known contacts; wearer shown with blue "Wearer" badge
- **PersonDetailView** — voice enrollment status, knowledge graph preview, full-screen graph canvas, recap
- **GraphCanvasView** — concentric depth-ring layout, pan + zoom, tap a node for its edges
