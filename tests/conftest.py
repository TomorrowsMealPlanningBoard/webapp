"""
テスト共通のfixture。
- インメモリSQLiteでDBを差し替える（本番DBに触れない）
- テスト用ユーザーの作成とJWTトークンの取得を提供する
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# app.main のインポートより先に database をインポートしてモデルを登録する
from app.database import Base, get_db
from app.models import User  # noqa: F401 — テーブル定義をBaseに登録するために必要
from app.auth import get_password_hash, create_access_token

TEST_DB_URL = "sqlite:///./test_tomorrows_meal.db"

@pytest.fixture(scope="session")
def test_engine():
    e = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=e)
    yield e
    Base.metadata.drop_all(bind=e)
    import os
    if os.path.exists("./test_tomorrows_meal.db"):
        os.remove("./test_tomorrows_meal.db")

@pytest.fixture(scope="function")
def db(test_engine):
    # テストごとにテーブルをリセットしてデータ汚染を防ぐ
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()

@pytest.fixture(scope="function")
def client(db, monkeypatch):
    # init_db() がテスト用DBに向くようにオーバーライドしてからappをインポート
    def override_get_db():
        yield db

    from app.main import app
    # init_db はモジュールロード済みなので、テスト時は何もしないようにパッチ
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture(scope="function")
def test_user(db):
    user = User(
        uid="test-user-001",
        email="test@example.com",
        hashed_password=get_password_hash("testpassword"),
        display_name="テストユーザー",
        preferences={
            "allergies": [],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": []
        }
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture(scope="function")
def auth_headers(test_user):
    token = create_access_token(data={"sub": test_user.uid})
    return {"Authorization": f"Bearer {token}"}
