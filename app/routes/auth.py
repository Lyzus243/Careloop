from typing import Optional
from fastapi import APIRouter, Depends, status, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.rate_limit import RateLimitedRouter, rate_limit
from app.database import get_db
from app.controllers.auth_controller import AuthController
from app.schemas.user import (
    UserCreate, UserCreateResponse, UserLogin, Token, UserResponse,
    ForgotPasswordRequest, ForgotPasswordResponse,
    ResetPasswordRequest, ResetPasswordResponse,
    ChangePasswordRequest, ChangePasswordResponse,
    SetInitialPasswordRequest, RefreshRequest, LogoutRequest
)
from app.dependencies import get_current_user_id

router = RateLimitedRouter(prefix="/auth", tags=["authentication"], limit="20/minute")
optional_security = HTTPBearer(auto_error=False)

@router.post("/signup", response_model=UserCreateResponse, status_code=status.HTTP_201_CREATED)
@rate_limit("10/minute")
async def signup(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    return await AuthController.create_user(db, user_data, base_url)

@router.post("/login", response_model=Token)
@rate_limit("10/minute")
async def login(
    request: Request,
    login_data: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    result = await AuthController.authenticate_user(db, login_data, ip_address=request.client.host)
    return result

@router.post("/refresh", response_model=Token)
@rate_limit("60/minute")
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.refresh_session(db, body.refresh_token)

@router.get("/verify-email")
async def verify_email(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    await AuthController.verify_email(db, token)
    try:
        with open("Frontend/email-verified.html", "r", encoding="utf-8") as f:
            html = f.read()
            html = html.replace('href="/create-password"', f'href="/create-password?token={token}"')
            return HTMLResponse(content=html)
    except FileNotFoundError:
        return HTMLResponse(
            content=f"<h1>Email verified successfully!</h1><p><a href='/create-password?token={token}'>Create Password</a></p>",
            status_code=200
        )

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@rate_limit("5/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    return await AuthController.forgot_password(db, body, base_url)

@router.post("/reset-password", response_model=ResetPasswordResponse)
@rate_limit("5/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.reset_password(db, body)

@router.post("/change-password", response_model=ChangePasswordResponse)
@rate_limit("5/minute")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.change_password(db, user_id, body)

@router.get("/me", response_model=UserResponse)
async def get_current_user(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.get_current_user(db, user_id)

@router.post("/logout")
async def logout(
    request: Request,
    body: Optional[LogoutRequest] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: AsyncSession = Depends(get_db)
):
    # Doesn't require a valid access token, so the refresh token is still
    # revoked when the user signs out after the access token has expired.
    tokens = [credentials.credentials if credentials else None, body.refresh_token if body else None]
    await AuthController.revoke_tokens(db, [t for t in tokens if t])
    return {"message": "Successfully logged out"}

@router.post("/set-initial-password")
@rate_limit("5/minute")
async def set_initial_password(
    request: Request,
    body: SetInitialPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.set_initial_password(db, body.token, body.password)

