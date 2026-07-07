from datetime import datetime, timedelta
from typing import Optional
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session
import os

from .database import get_db
from .models import User

# パスワード暗号化コンテキスト
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT署名用の秘密鍵とアルゴリズム
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY が設定されていません")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7日間有効

# Google OAuth2 クライアントID（未設定時はパスワード認証にフォールバック）
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

# FastAPI標準の OAuth2PasswordBearer
# tokenUrl はログインエンドポイントのURL
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_google_id_token(id_token: str) -> dict:
    """
    Google が発行した id_token を検証し、ペイロード（sub, email, name 等）を返す。
    GOOGLE_CLIENT_ID が未設定または検証失敗時は HTTPException(401) を送出する。
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google認証が設定されていません。",
        )
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        idinfo = google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
        return idinfo
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Googleトークンの検証に失敗しました。",
        )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ログインセッションが無効、または期限切れです。",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid: str = payload.get("sub")
        if uid is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.uid == uid).first()
    if user is None:
        raise credentials_exception
    return user


def get_current_user_from_token(token: str, db: Session) -> Optional[User]:
    """
    WebSocketエンドポイント用の認証ヘルパー。

    WebSocketはAuthorizationヘッダーを使う `OAuth2PasswordBearer` に乗れないため、
    クエリパラメータ等で渡されたトークン文字列を直接検証する。
    無効な場合は None を返す（呼び出し側でWebSocketをclose(1008)すること）。
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid: str = payload.get("sub")
        if uid is None:
            return None
    except jwt.PyJWTError:
        return None

    return db.query(User).filter(User.uid == uid).first()


def get_rate_limit_key(request: Request) -> str:
    """
    レート制限のキーとしてユーザーID（JWTのsub）を使う。
    未認証・トークン不正の場合はクライアントIPにフォールバックする
    （認証エラー自体は各エンドポイントのDependsが別途401を返す）。
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                return f"user:{uid}"
        except jwt.PyJWTError:
            pass
    return f"ip:{request.client.host if request.client else 'unknown'}"
