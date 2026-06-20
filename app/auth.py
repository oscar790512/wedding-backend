from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.database import get_supabase

security = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.jwt_expire_minutes,
    )
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def authenticate_admin(username: str, password: str) -> dict | None:
    response = (
        get_supabase()
        .table("admin_users")
        .select("username, password_hash, role")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None

    user = response.data[0]
    if not verify_password(password, user["password_hash"]):
        return None

    return {"username": user["username"], "role": user["role"]}


def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        username = payload.get("sub")
        role = payload.get("role")
        if not username or not role:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    return {"username": username, "role": role}
