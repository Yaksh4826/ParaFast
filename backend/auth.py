"""Central authentication module — JWT-based auth using the profiles table."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

JWT_SECRET = os.getenv("JWT_SECRET", "changeme")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
COOKIE_NAME = "access_token"
COOKIE_MAX_AGE = 24 * 60 * 60  # 24 hours in seconds

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(badge_number: str, profile_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": badge_number,
        "pid": profile_id,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc


def _token_from_request(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    """Get token from Authorization header or cookie."""
    if credentials and credentials.credentials:
        return credentials.credentials
    return request.cookies.get(COOKIE_NAME)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """Extracts JWT from Authorization header or cookie. Returns payload with sub (badge_number)."""
    token = _token_from_request(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    payload = decode_token(token)
    badge = payload.get("sub")
    if not badge:
        raise HTTPException(status_code=401, detail="Invalid token payload.")
    return payload


def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any] | None:
    """Same as get_current_user but returns None when not authenticated."""
    token = _token_from_request(request, credentials)
    if not token:
        return None
    try:
        payload = decode_token(token)
        badge = payload.get("sub")
        return payload if badge else None
    except (HTTPException, JWTError):
        return None
