"""
Google OAuth2 認証のユニットテスト（Issue #90）
"""
from unittest.mock import MagicMock, patch

from app.auth import create_access_token


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_google_login_success(client, db):
    """Google id_token 検証成功 → JWT が発行される（初回ログイン: ユーザーが自動作成される）"""
    fake_idinfo = {
        "sub": "google-sub-001",
        "email": "google_user@example.com",
        "name": "Googleユーザー",
    }
    with patch("app.main.verify_google_id_token", return_value=fake_idinfo):
        res = client.post("/api/auth/google", json={"id_token": "fake-token"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_google_login_creates_user_on_first_login(client, db):
    """初回 Google ログインでユーザーレコードが自動作成される"""
    from app.models import User

    fake_idinfo = {
        "sub": "google-sub-new",
        "email": "newuser@example.com",
        "name": "新規ユーザー",
    }
    with patch("app.main.verify_google_id_token", return_value=fake_idinfo):
        res = client.post("/api/auth/google", json={"id_token": "fake-token"})
    assert res.status_code == 200

    user = db.query(User).filter(User.uid == "google-sub-new").first()
    assert user is not None
    assert user.email == "newuser@example.com"
    assert user.display_name == "新規ユーザー"
    assert user.hashed_password is None


def test_google_login_existing_user(client, db):
    """同じ Google アカウントで再ログイン → 既存ユーザーとして認識（新規作成されない）"""
    from app.models import User

    existing = User(
        uid="google-sub-existing",
        email="existing@example.com",
        hashed_password=None,
        display_name="既存ユーザー",
        preferences={"allergies": [], "dislikes": [], "goal": "none", "kitchen_tools": []},
    )
    db.add(existing)
    db.commit()

    fake_idinfo = {
        "sub": "google-sub-existing",
        "email": "existing@example.com",
        "name": "既存ユーザー",
    }
    with patch("app.main.verify_google_id_token", return_value=fake_idinfo):
        res = client.post("/api/auth/google", json={"id_token": "fake-token"})
    assert res.status_code == 200

    count = db.query(User).filter(User.email == "existing@example.com").count()
    assert count == 1


def test_google_login_invalid_token(client):
    """不正な id_token → 401"""
    with patch("app.auth.GOOGLE_CLIENT_ID", "dummy-client-id"):
        res = client.post("/api/auth/google", json={"id_token": "bad-token"})
    assert res.status_code == 401


def test_google_login_no_client_id(client):
    """GOOGLE_CLIENT_ID 未設定 → 401"""
    with patch("app.auth.GOOGLE_CLIENT_ID", None):
        res = client.post("/api/auth/google", json={"id_token": "any-token"})
    assert res.status_code == 401


def test_auth_config_with_client_id(client):
    """GOOGLE_CLIENT_ID 設定済み → /api/auth/config にクライアントIDが返る"""
    with patch("app.main.GOOGLE_CLIENT_ID", "my-client-id.apps.googleusercontent.com"):
        res = client.get("/api/auth/config")
    assert res.status_code == 200
    assert res.json()["google_client_id"] == "my-client-id.apps.googleusercontent.com"


def test_auth_config_without_client_id(client):
    """GOOGLE_CLIENT_ID 未設定 → /api/auth/config は空文字を返す"""
    with patch("app.main.GOOGLE_CLIENT_ID", None):
        res = client.get("/api/auth/config")
    assert res.status_code == 200
    assert res.json()["google_client_id"] == ""


def test_get_profile_with_google_user(client, db):
    """Google OAuth で作成したユーザーが /api/profile にアクセスできる"""
    from app.models import User

    user = User(
        uid="google-sub-profile",
        email="profile@example.com",
        hashed_password=None,
        display_name="プロファイルユーザー",
        preferences={"allergies": [], "dislikes": [], "goal": "none", "kitchen_tools": []},
    )
    db.add(user)
    db.commit()

    token = create_access_token(data={"sub": user.uid})
    res = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["email"] == "profile@example.com"


def test_get_profile_unauthorized(client):
    res = client.get("/api/profile")
    assert res.status_code == 401
