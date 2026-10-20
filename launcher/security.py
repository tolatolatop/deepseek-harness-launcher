import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from sqlmodel import Session

from launcher.models import LoginSession, User, now
from launcher.settings import settings

_passwords = PasswordHash.recommended()


def password_hash(value: str) -> str:
    return _passwords.hash(value)


def verify_password(value: str, hashed: str) -> bool:
    return _passwords.verify(value, hashed)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    try:
        return Fernet(settings().secret_key.encode())
    except (ValueError, TypeError):
        raise RuntimeError("LAUNCHER_SECRET_KEY must be a Fernet key; run scripts/init-env.py") from None


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    return cipher().decrypt(value.encode()).decode() if value else ""


def csrf_token(token: str) -> str:
    return hmac.new(settings().secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=digest(token), user_id=user.id, expires_at=now() + settings().session_hours * 3600
        )
    )
    db.commit()
    return token


def authenticated(db: Session, token: str | None) -> User:
    session = db.get(LoginSession, digest(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, "请先登录")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "账号已停用或会话已失效")
    return user


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin != settings().origin:
        raise HTTPException(403, "请求来源不受信任")


def current_user(request: Request, db: Session) -> User:
    token = request.cookies.get("launcher_session")
    user = authenticated(db, token)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token(token)):
            raise HTTPException(403, "页面会话已变化，请刷新后重试")
    return user
