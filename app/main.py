from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import os

from .database import engine, Base, get_db
from .models import User
from .schemas import UserProfileUpdate, UserResponse

# データベーステーブルの作成
Base.metadata.create_all(bind=engine)

app = FastAPI(title="desLunch API")

# 初期データの作成（default_userが存在しない場合）
def init_db():
    db = next(get_db())
    try:
        default_user = db.query(User).filter(User.uid == "default_user").first()
        if not default_user:
            new_user = User(
                uid="default_user",
                email="default@example.com",
                display_name="ゲストユーザー",
                preferences={
                    "allergies": [],
                    "dislikes": [],
                    "goal": "other"
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

# プロファイル取得API
@app.get("/api/profile", response_model=UserResponse)
def get_profile(db: Session = Depends(get_db)):
    user = db.query(User).filter(User.uid == "default_user").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# プロファイル更新API
@app.put("/api/profile", response_model=UserResponse)
def update_profile(profile_data: UserProfileUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.uid == "default_user").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if profile_data.display_name is not None:
        user.display_name = profile_data.display_name
        
    if profile_data.preferences is not None:
        user.preferences = profile_data.preferences.model_dump()
        
    db.commit()
    db.refresh(user)
    return user


