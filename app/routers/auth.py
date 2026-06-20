from fastapi import APIRouter, HTTPException, status

from app.auth import authenticate_admin, create_access_token
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    user = authenticate_admin(payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(user["username"], user["role"])
    return LoginResponse(
        access_token=token,
        role=user["role"],
        username=user["username"],
    )
