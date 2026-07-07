"""
Issue #22: Google Health API連携と健康データの取得 ユニットテスト

- HealthData dataclass が calories/protein_g/fat_g/carbs_g フィールドを持つこと
- GOOGLE_FIT_ACCESS_TOKEN が未設定の場合は None を返すこと（オプション扱い）
- Google Fit REST API を呼び出してデータを取得できること（モック使用）
- API 呼び出しエラー時は例外を飲み込んで None を返すこと
- ContextRetrieverAgent の retrieve() が health_data を RetrievedContext に含めること
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.health_api import HealthData, HealthDataClient
from app.agents.context_retriever import ContextRetrieverAgent, RetrievedContext
from app.models import User


# ------------------------------------------------------------------ helpers --

def _make_user(db, uid="health-user-001"):
    user = User(
        uid=uid,
        email=f"{uid}@example.com",
        hashed_password=None,
        display_name="ヘルスAPIテストユーザー",
        preferences={
            "allergies": [],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
        },
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


_SAMPLE_FIT_RESPONSE = {
    "bucket": [
        {
            "dataset": [
                {
                    "dataSourceId": "derived:com.google.calories.consumed:com.google.android.gms:merged",
                    "point": [
                        {
                            "dataTypeName": "com.google.calories.consumed",
                            "value": [{"fpVal": 1850.5}],
                        }
                    ],
                },
                {
                    "dataSourceId": "derived:com.google.nutrition:com.google.android.gms:merged",
                    "point": [
                        {
                            "dataTypeName": "com.google.nutrition",
                            "value": [
                                {
                                    "mapVal": [
                                        {"key": "protein.total.g", "value": {"fpVal": 72.3}},
                                        {"key": "fat.total.g", "value": {"fpVal": 45.1}},
                                        {"key": "carbs.total.g", "value": {"fpVal": 230.8}},
                                    ]
                                }
                            ],
                        }
                    ],
                },
            ]
        }
    ]
}


# ------------------------------------------------------------------ HealthData --

def test_health_data_has_required_fields():
    """HealthData が calories/protein_g/fat_g/carbs_g フィールドを持つこと"""
    hd = HealthData()
    assert hd.calories is None
    assert hd.protein_g is None
    assert hd.fat_g is None
    assert hd.carbs_g is None


def test_health_data_accepts_float_values():
    """HealthData に float 値を設定できること"""
    hd = HealthData(calories=2000.0, protein_g=80.0, fat_g=50.0, carbs_g=250.0)
    assert hd.calories == 2000.0
    assert hd.protein_g == 80.0
    assert hd.fat_g == 50.0
    assert hd.carbs_g == 250.0


# ------------------------------------------------------------------ HealthDataClient --

def test_health_data_client_returns_none_when_no_token(monkeypatch):
    """GOOGLE_FIT_ACCESS_TOKEN が未設定の場合は None を返すこと（オプション扱い）"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)
    client = HealthDataClient()
    result = asyncio.run(client.get_yesterday_health_data())
    assert result is None


def test_health_data_client_returns_none_when_env_empty(monkeypatch):
    """GOOGLE_FIT_ACCESS_TOKEN が空文字の場合も None を返すこと"""
    monkeypatch.setenv("GOOGLE_FIT_ACCESS_TOKEN", "")
    client = HealthDataClient()
    result = asyncio.run(client.get_yesterday_health_data())
    assert result is None


def test_health_data_client_uses_provided_token(monkeypatch):
    """コンストラクタに渡した access_token を優先して使うこと"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)
    client = HealthDataClient(access_token="test-token")
    assert client._is_configured() is True


def test_health_data_client_fetches_data_via_api(monkeypatch):
    """Google Fit REST API を呼び出してデータを正しくパースすること（モック使用）"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _SAMPLE_FIT_RESPONSE

    async def mock_post(*args, **kwargs):
        return mock_response

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_async_client

        client = HealthDataClient(access_token="valid-token")
        result = asyncio.run(client.get_yesterday_health_data())

    assert result is not None
    assert result.calories == 1850.5
    assert result.protein_g == 72.3
    assert result.fat_g == 45.1
    assert result.carbs_g == 230.8


def test_health_data_client_returns_none_on_http_error(monkeypatch):
    """HTTP エラー時は例外を飲み込んで None を返すこと"""
    import httpx

    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client.post = AsyncMock(side_effect=httpx.RequestError("connection failed"))
        mock_client_class.return_value = mock_async_client

        client = HealthDataClient(access_token="valid-token")
        result = asyncio.run(client.get_yesterday_health_data())

    assert result is None


def test_health_data_client_returns_none_on_unexpected_exception(monkeypatch):
    """予期しない例外でも None を返してエラーにならないこと"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_client_class.return_value = mock_async_client

        client = HealthDataClient(access_token="valid-token")
        result = asyncio.run(client.get_yesterday_health_data())

    assert result is None


def test_health_data_client_handles_empty_response(monkeypatch):
    """API が空のバケットを返した場合でも HealthData を返すこと"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"bucket": []}

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_async_client

        client = HealthDataClient(access_token="valid-token")
        result = asyncio.run(client.get_yesterday_health_data())

    assert result is not None
    assert result.calories is None
    assert result.protein_g is None


# ------------------------------------------------------------------ ContextRetriever integration --

def test_context_retriever_includes_health_data_in_result(db, monkeypatch):
    """ContextRetrieverAgent.retrieve() が health_data を RetrievedContext に含めること"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)
    user = _make_user(db)

    mock_health_data = HealthData(calories=2000.0, protein_g=80.0, fat_g=50.0, carbs_g=250.0)
    mock_client = MagicMock(spec=HealthDataClient)
    mock_client.get_yesterday_health_data = AsyncMock(return_value=mock_health_data)

    agent = ContextRetrieverAgent(db=db, health_data_client=mock_client)
    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert isinstance(result, RetrievedContext)
    assert result.health_data is not None
    assert result.health_data.calories == 2000.0
    assert result.health_data.protein_g == 80.0
    assert result.health_data.fat_g == 50.0
    assert result.health_data.carbs_g == 250.0


def test_context_retriever_health_data_is_none_when_not_configured(db, monkeypatch):
    """GOOGLE_FIT_ACCESS_TOKEN 未設定時は health_data が None であり例外にならないこと"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)
    user = _make_user(db, uid="health-user-002")

    agent = ContextRetrieverAgent(db=db)
    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert isinstance(result, RetrievedContext)
    assert result.health_data is None


def test_context_retriever_skips_health_data_on_api_error(db, monkeypatch):
    """Health API でエラーが発生しても retrieve() がエラーにならないこと"""
    monkeypatch.delenv("GOOGLE_FIT_ACCESS_TOKEN", raising=False)
    user = _make_user(db, uid="health-user-003")

    mock_client = MagicMock(spec=HealthDataClient)
    mock_client.get_yesterday_health_data = AsyncMock(return_value=None)

    agent = ContextRetrieverAgent(db=db, health_data_client=mock_client)
    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert isinstance(result, RetrievedContext)
    assert result.health_data is None
