import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.user import User
from app.services.auth import decode_access_token

_bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    try:
        user_id: uuid.UUID = decode_access_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_enrolled_user(user: User = Depends(get_current_user)) -> User:
    """Dependency for routes that require voice enrollment."""
    if not user.voice_enrolled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Voice enrollment required before starting sessions",
        )
    return user
