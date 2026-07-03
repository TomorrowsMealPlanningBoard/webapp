"""
テスト共通のfixture。
- インメモリSQLiteでDBを差し替える（本番DBに触れない）
- テスト用ユーザーの作成とJWTトークンの取得を提供する
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.auth import get_password_hash, create_access_token
from app.models import User

TEST_DB_URL = "sqlite:///:memory:"

@pytest.fixture(scope="session")
def engine():
    e = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=e)
    yield e
    Base.metadata.drop_all(bind=e)

@pytest.fixture(scope="function")
def db(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()

@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
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
