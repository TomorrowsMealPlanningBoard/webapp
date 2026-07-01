from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import os
import uuid

from .database import engine, Base, get_db
from .models import User
from .schemas import UserProfileUpdate, UserResponse, UserRegister, Token
from .auth import get_password_hash, verify_password, create_access_token, get_current_user

# データベーステーブルの作成
Base.metadata.create_all(bind=engine)

app = FastAPI(title="TomorrowsMeal API")


# 初期データの作成（default_userが存在しない場合）
def init_db():
    db = next(get_db())
    try:
        default_user = db.query(User).filter(User.uid == "default_user").first()
        if not default_user:
            new_user = User(
                uid="default_user",
                email="guest@example.com",
                hashed_password=get_password_hash("password"),
                display_name="ゲストユーザー",
                preferences={
                    "allergies": [],
                    "dislikes": [],
                    "goal": "other",
                    "kitchen_tools": []
                }
            )
            db.add(new_user)
            db.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        db.close()


init_db()

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/health")
def health_check():
    return {"status": "ok"}

# アカウント作成 API（JSON形式）
@app.post("/api/auth/register", response_model=Token)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています。")

    uid = str(uuid.uuid4())
    hashed_pwd = get_password_hash(user_data.password)
    new_user = User(
        uid=uid,
        email=user_data.email,
        hashed_password=hashed_pwd,
        display_name=user_data.display_name or user_data.email.split("@")[0],
        preferences={
            "allergies": [],
            "dislikes": [],
            "goal": "none",
            "kitchen_tools": []
        }
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    access_token = create_access_token(data={"sub": new_user.uid})
    return {"access_token": access_token, "token_type": "bearer"}

# ログイン API（FastAPI標準の OAuth2PasswordRequestForm を使用）
# username フィールドにメールアドレスを入力する
@app.post("/api/auth/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # OAuth2の仕様では username フィールドを使う（ここではメールアドレスを受け取る）
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="メールアドレスまたはパスワードが正しくありません。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.uid})
    return {"access_token": access_token, "token_type": "bearer"}

# プロファイル取得API
@app.get("/api/profile", response_model=UserResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    return current_user


# プロファイル更新API
@app.put("/api/profile", response_model=UserResponse)
def update_profile(
    profile_data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if profile_data.display_name is not None:
        current_user.display_name = profile_data.display_name

    if profile_data.preferences is not None:
        current_user.preferences = profile_data.preferences.model_dump()

    db.commit()
    db.refresh(current_user)
    return current_user
