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


def create_access_token(username: str, role: str, token_version: int) -> str:
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.jwt_expire_minutes,
    )
    payload = {
        "sub": username,
        "role": role,
        "ver": token_version,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def authenticate_admin(username: str, password: str) -> dict | None:
    normalized_username = username.strip().lower()
    query = (
        get_supabase()
        .table("admin_users")
        .select(
            "username,display_name,password_hash,role,is_active,token_version",
        )
    )
    response = query.eq("username", normalized_username).limit(1).execute()
    if not response.data and username != normalized_username:
        response = (
            get_supabase()
            .table("admin_users")
            .select(
                "username,display_name,password_hash,role,is_active,token_version",
            )
            .eq("username", username)
            .limit(1)
            .execute()
        )
    if not response.data:
        return None

    user = response.data[0]
    if not user["is_active"] or not verify_password(
        password,
        user["password_hash"],
    ):
        return None

    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "token_version": user["token_version"],
    }


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
        token_version = payload.get("ver")
        if not username or not role or not isinstance(token_version, int):
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    response = (
        get_supabase()
        .table("admin_users")
        .select("username,display_name,role,is_active,token_version")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise credentials_exception

    user = response.data[0]
    if (
        not user["is_active"]
        or user["role"] != role
        or user["token_version"] != token_version
    ):
        raise credentials_exception

    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "token_version": user["token_version"],
    }


def require_admin(current_user: dict = Depends(get_current_admin)) -> dict:
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理員權限",
        )
    return current_user
