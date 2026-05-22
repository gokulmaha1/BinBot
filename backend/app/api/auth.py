"""
BinBot AI Auto Mode — Authentication API
JWT-based login/logout with role-based access control.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
import bcrypt
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.deps import get_session, get_current_user
from app.models import User, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["Authentication"])
pwd_context = None # deprecated passlib context

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Request/Response Models ──────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class UserResponse(BaseModel):
    user_id: str
    username: str
    role: str


# ── Token Helpers ────────────────────────────────────────────────

def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "role": role, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": user_id, "role": role, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


# ── Routes ───────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_session)):
    """Authenticate user and return JWT tokens."""
    # Check against DB user first
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if user and verify_password(req.password, user.password_hash):
        access = create_access_token(str(user.id), user.role.value)
        refresh = create_refresh_token(str(user.id), user.role.value)
        logger.info(f"User '{req.username}' logged in successfully")
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    # Fallback: check env-based admin credentials (bootstrap login)
    if req.username == settings.DASHBOARD_USER and req.password == settings.DASHBOARD_PASS:
        # Create admin user if not exists
        if not user:
            user = User(
                username=req.username,
                password_hash=hash_password(req.password),
                role=UserRole.ADMIN,
            )
            db.add(user)
            await db.flush()
            logger.info(f"Bootstrap admin user '{req.username}' created")

        access = create_access_token(str(user.id), UserRole.ADMIN.value)
        refresh = create_refresh_token(str(user.id), UserRole.ADMIN.value)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid username or password",
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """Refresh an expired access token."""
    try:
        payload = jwt.decode(
            refresh_token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        role = payload.get("role", "user")
        access = create_access_token(user_id, role)
        new_refresh = create_refresh_token(user_id, role)
        return TokenResponse(
            access_token=access,
            refresh_token=new_refresh,
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user info."""
    return UserResponse(
        user_id=current_user["user_id"],
        username="",  # Filled from DB if needed
        role=current_user["role"],
    )
