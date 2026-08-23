from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.login_attempt import LoginAttempt
from app.schemas.user import (
    UserCreate, UserCreateResponse, UserLogin, Token, UserResponse,
    UserUpdate,
    EmailVerificationRequest, EmailVerificationResponse,
    ForgotPasswordRequest, ForgotPasswordResponse,
    ResetPasswordRequest, ResetPasswordResponse,
    ChangePasswordRequest, ChangePasswordResponse,
    SetInitialPasswordRequest
)
from app.services.auth_service import TokenService, PasswordService, TokenGeneratorService
from app.services.email_service import email_service

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

class AuthController:

    @staticmethod
    async def create_user(
        db: AsyncSession,
        user_data: UserCreate,
        base_url: str = "https://mycareloop.com.ng"
    ) -> UserCreateResponse:
        try:
            existing_user = await AuthController._get_user_by_email(db, user_data.email)
            if existing_user:
                if not existing_user.is_email_verified:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="This email was never verified. Please sign up with a fresh email address."
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )

            hashed_password = None
            if user_data.password:
                if not PasswordService.validate_password_strength(user_data.password):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Password must be at least 8 characters long"
                    )
                hashed_password = PasswordService.hash_password(user_data.password)

            db_user = User(
                email=user_data.email,
                full_name=user_data.full_name,
                business_name=user_data.business_name,
                phone=user_data.phone_number,
                hashed_password=hashed_password,
                is_email_verified=False,
                is_active=True
            )

            db.add(db_user)
            await db.commit()
            await db.refresh(db_user)

            verification_token = TokenGeneratorService.generate_verification_token()
            verification_expires = datetime.utcnow() + timedelta(hours=24)

            db_user.email_verification_token = verification_token
            db_user.email_verification_expires_at = verification_expires
            await db.commit()

            try:
                print(f"DEBUG signup base_url: {base_url}")
                await email_service.send_verification_email(user_data.email, verification_token, base_url)
            except Exception as e:
                print(f"Failed to send verification email: {e}")
                print(f"Verification token for manual testing: {verification_token}")

            return UserCreateResponse(
                message="User created successfully. Please check your email for verification.",
                user=UserResponse(
                    id=db_user.id,
                    email=db_user.email,
                    full_name=db_user.full_name,
                    business_name=db_user.business_name,
                    is_active=db_user.is_active,
                    created_at=db_user.created_at
                ),
                verification_token=verification_token
            )

        except HTTPException:
            raise
        except Exception as e:
            print(f"Database error in create_user: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Internal server error: {str(e)}"
            )

    @staticmethod
    async def authenticate_user(
        db: AsyncSession,
        login_data: UserLogin,
        ip_address: Optional[str] = None
    ) -> Token:
        lockout_window = datetime.utcnow() - timedelta(minutes=LOCKOUT_MINUTES)
        recent_attempts = await db.execute(
            select(LoginAttempt).where(
                LoginAttempt.email == login_data.email,
                LoginAttempt.attempted_at >= lockout_window,
                LoginAttempt.success == False
            )
        )
        failed_attempts = recent_attempts.scalars().all()

        if len(failed_attempts) >= MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Account locked due to too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes."
            )

        user = await AuthController._get_user_by_email(db, login_data.email)

        async def record_attempt(success: bool):
            attempt = LoginAttempt(
                email=login_data.email,
                ip_address=ip_address,
                success=success
            )
            db.add(attempt)
            await db.commit()

        if not user:
            await record_attempt(False)
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not user.is_email_verified:
            raise HTTPException(status_code=401, detail="Email not verified")

        if not user.is_active:
            raise HTTPException(status_code=401, detail="Account deactivated")

        if not PasswordService.verify_password(login_data.password, user.hashed_password):
            await record_attempt(False)
            raise HTTPException(status_code=401, detail="Invalid email or password")

        await record_attempt(True)

        user.last_login_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)

        access_token_expires = timedelta(minutes=60)
        access_token = TokenService.create_access_token(
            data={"sub": str(user.id), "email": user.email},
            expires_delta=access_token_expires
        )

        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=int(access_token_expires.total_seconds()),
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                business_name=user.business_name,
                is_active=user.is_active,
                created_at=user.created_at,
                updated_at=user.updated_at,
                last_login_at=user.last_login_at
            )
        )

    @staticmethod
    async def verify_email(db: AsyncSession, token: str) -> dict:
        result = await db.execute(
            select(User).where(User.email_verification_token == token)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token"
            )

        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification token has expired"
            )

        user.is_email_verified = True
        await db.commit()
        return {"message": "Email verified successfully"}

    @staticmethod
    async def forgot_password(db: AsyncSession, request: ForgotPasswordRequest, base_url: str = "https://mycareloop.com.ng") -> ForgotPasswordResponse:
        user = await AuthController._get_user_by_email(db, request.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sorry, there is no user associated with this email address."
            )
        if not user.is_email_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This email was never verified. Please sign up with a fresh email address."
            )

        reset_token = TokenGeneratorService.generate_password_reset_token()
        reset_expires = datetime.utcnow() + timedelta(hours=1)

        user.password_reset_token = reset_token
        user.password_reset_expires_at = reset_expires
        await db.commit()

        print(f"DEBUG: Sending reset email to {user.email} with token {reset_token}")
        await email_service.send_password_reset_email(user.email, reset_token, base_url)

        return ForgotPasswordResponse(
            message="If an account with this email exists, a password reset link has been sent."
        )

    @staticmethod
    async def reset_password(db: AsyncSession, request: ResetPasswordRequest) -> ResetPasswordResponse:
        result = await db.execute(
            select(User).where(User.password_reset_token == request.token)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )

        if user.password_reset_expires_at and user.password_reset_expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired"
            )

        if not PasswordService.validate_password_strength(request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters long"
            )

        user.hashed_password = PasswordService.hash_password(request.new_password)
        user.password_reset_token = None
        user.password_reset_expires_at = None
        await db.commit()
        return ResetPasswordResponse(message="Password reset successfully")

    @staticmethod
    async def change_password(db: AsyncSession, user_id: int, request: ChangePasswordRequest) -> ChangePasswordResponse:
        user = await AuthController._get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if not PasswordService.verify_password(request.current_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

        if not PasswordService.validate_password_strength(request.new_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters long")

        user.hashed_password = PasswordService.hash_password(request.new_password)
        await db.commit()
        return ChangePasswordResponse(message="Password changed successfully")

    @staticmethod
    async def set_initial_password(db: AsyncSession, token: str, password: str) -> dict:
        result = await db.execute(
            select(User).where(User.email_verification_token == token)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification token")

        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token has expired")

        if not PasswordService.validate_password_strength(password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters long and contain upper/lowercase and a number"
            )

        user.hashed_password = PasswordService.hash_password(password)
        user.is_email_verified = True
        user.email_verification_token = None
        user.email_verification_expires_at = None
        await db.commit()
        return {"message": "Password created successfully. You can now log in."}

    @staticmethod
    async def get_current_user(db: AsyncSession, user_id: int) -> UserResponse:
        user = await AuthController._get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        return UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            business_name=user.business_name,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login_at=user.last_login_at,
            preferred_currency=getattr(user, 'preferred_currency', 'USD')
        )

    @staticmethod
    async def update_user_profile(db: AsyncSession, user_id: int, user_data: UserUpdate) -> UserResponse:
        user = await AuthController._get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if user_data.full_name is not None:
            user.full_name = user_data.full_name
        if user_data.business_name is not None:
            user.business_name = user_data.business_name

        user.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)

        return UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            business_name=user.business_name,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login_at=user.last_login_at,
            preferred_currency=getattr(user, 'preferred_currency', 'USD')
        )

    @staticmethod
    async def update_user_avatar(db: AsyncSession, user_id: int, avatar_url: str) -> dict:
        user = await AuthController._get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        user.avatar = avatar_url
        user.updated_at = datetime.utcnow()
        await db.commit()
        return {"message": "Avatar updated successfully", "avatar": avatar_url}

    @staticmethod
    async def _get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
