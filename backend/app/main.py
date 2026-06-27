from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import auth, sessions, people
from app.ws import stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.2)
    yield


app = FastAPI(title="Jarvis API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(people.router)
app.include_router(stream.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "environment": settings.environment}
