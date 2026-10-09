from datetime import datetime, timedelta
from typing import Optional
import jwt
import uuid
from passlib.context import CryptContext
from fastapi import HTTPException, status
import hashlib
import hmac
import secrets
import os
import re

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set. Add it to your .env file.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
# A session lasts this long without the app being opened; each refresh restarts it.
REFRESH_TOKEN_EXPIRE_DAYS = 30

class TokenService:
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
        to_encode = data.copy()
        expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
        jti = str(uuid.uuid4())
        to_encode.update({"exp": expire, "jti": jti})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt

    @staticmethod
    def create_refresh_token(user_id: int, hashed_password: Optional[str]) -> str:
        return TokenService.create_access_token(
            data={
                "sub": str(user_id),
                "type": "refresh",
                "pwd": TokenService.password_fingerprint(hashed_password),
            },
            expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        )

    @staticmethod
    def password_fingerprint(hashed_password: Optional[str]) -> str:
        """Ties a refresh token to the password it was issued under, so changing
        or resetting the password signs out every other device."""
        return hmac.new(
            SECRET_KEY.encode(), (hashed_password or "").encode(), hashlib.sha256
        ).hexdigest()[:16]

    @staticmethod
    def verify_token(token: str):
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @staticmethod
    def get_token_data(token: str):
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except jwt.PyJWTError:
            return None

    @staticmethod
    def get_jti(token: str) -> Optional[str]:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload.get("jti")
        except jwt.PyJWTError:
            return None

class PasswordService:
    @staticmethod
    def hash_password(password: str) -> str:
        # bcrypt max is 72 bytes — truncate to be safe
        password = password[:72]
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        plain_password = plain_password[:72]
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def validate_password_strength(password: str) -> bool:
        if not password:
            return True
        if len(password) < 8:
            return False
        if not re.search(r"[A-Z]", password):
            return False
        if not re.search(r"[a-z]", password):
            return False
        if not re.search(r"\d", password):
            return False
        return True

class TokenGeneratorService:
    @staticmethod
    def generate_verification_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def generate_password_reset_token() -> str:
        return secrets.token_urlsafe(32)
