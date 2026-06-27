import secrets
import uuid
from datetime import datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.services.redis_client import get_redis

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HS256 in dev; swap to RS256 with Doppler-injected keys in Chunk 9
_ALGORITHM = "HS256"
_REFRESH_PREFIX = "refresh:"


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise ValueError("invalid token") from exc
    if payload.get("type") != "access":
        raise ValueError("wrong token type")
    return uuid.UUID(payload["sub"])


async def create_refresh_token(user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    ttl = int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
    await get_redis().setex(f"{_REFRESH_PREFIX}{token}", ttl, str(user_id))
    return token


async def verify_refresh_token(token: str) -> uuid.UUID:
    raw = await get_redis().get(f"{_REFRESH_PREFIX}{token}")
    if not raw:
        raise ValueError("refresh token invalid or expired")
    return uuid.UUID(raw)


async def revoke_refresh_token(token: str) -> None:
    await get_redis().delete(f"{_REFRESH_PREFIX}{token}")


async def revoke_all_refresh_tokens(user_id: uuid.UUID) -> None:
    """Used by DELETE /account to invalidate all sessions."""
    pattern = f"{_REFRESH_PREFIX}*"
    cursor = 0
    redis = get_redis()
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=100)
        for key in keys:
            val = await redis.get(key)
            if val == str(user_id):
                await redis.delete(key)
        if cursor == 0:
            break
