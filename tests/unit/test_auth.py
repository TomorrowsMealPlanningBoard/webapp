"""
Epic 1-1 / 1-2: ユーザー登録・ログイン・プロファイル取得のユニットテスト
"""


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_register_success(client):
    res = client.post("/api/auth/register", json={
        "email": "new@example.com",
        "password": "newpassword",
        "display_name": "新規ユーザー"
    })
    assert res.status_code == 200
    assert "access_token" in res.json()


def test_register_duplicate_email(client, test_user):
    res = client.post("/api/auth/register", json={
        "email": test_user.email,
        "password": "whatever",
        "display_name": "重複"
    })
    assert res.status_code == 400


def test_login_success(client, test_user):
    res = client.post("/api/auth/login", data={
        "username": test_user.email,
        "password": "testpassword"
    })
    assert res.status_code == 200
    assert "access_token" in res.json()


def test_login_wrong_password(client, test_user):
    res = client.post("/api/auth/login", data={
        "username": test_user.email,
        "password": "wrongpassword"
    })
    assert res.status_code == 401


def test_get_profile(client, test_user, auth_headers):
    res = client.get("/api/profile", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["email"] == test_user.email


def test_get_profile_unauthorized(client):
    res = client.get("/api/profile")
    assert res.status_code == 401


def test_update_profile(client, test_user, auth_headers):
    res = client.put("/api/profile", headers=auth_headers, json={
        "display_name": "更新後の名前",
        "preferences": {
            "allergies": ["そば"],
            "dislikes": [],
            "goal": "health",
            "kitchen_tools": ["電子レンジ"]
        }
    })
    assert res.status_code == 200
    assert res.json()["display_name"] == "更新後の名前"
    assert "そば" in res.json()["preferences"]["allergies"]
