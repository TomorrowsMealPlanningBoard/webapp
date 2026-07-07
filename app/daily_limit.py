"""
ユーザー別1日リクエスト上限管理（Issue #88 / 課金暴走防止）。

Firestore: users/{uid}/daily_usage/{YYYY-MM-DD} ドキュメントを日付キーとして使用。
日付が変わると自然にリセットされる（古いドキュメントは放置してよい。課金への影響は無視できる）。

ローカル開発時は USE_FIRESTORE 未設定でインメモリ辞書にフォールバックする。
テスト時（PYTEST_CURRENT_TEST）は常にインメモリを使用する。
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import NamedTuple

# JST = UTC+9
_JST_OFFSET = 9 * 3600

DAILY_LIMITS: dict[str, int] = {
    "propose": 10,
    "vision": 5,
    "voice_seconds": 180,
}


class UsageStatus(NamedTuple):
    allowed: bool
    current: int
    limit: int
    reset_at_jst: str  # "明日 00:00 JST"


def _today_jst() -> str:
    """JSTの今日の日付を YYYY-MM-DD で返す。"""
    now_utc = datetime.now(timezone.utc)
    jst_ts = now_utc.timestamp() + _JST_OFFSET
    d = date.fromtimestamp(jst_ts)
    return d.strftime("%Y-%m-%d")


def _reset_label() -> str:
    return "明日 00:00 JST"


# ---------------------------------------------------------------------------
# インメモリ実装（ローカル開発・テスト用）
# ---------------------------------------------------------------------------

_in_memory: dict[str, dict[str, dict[str, int]]] = {}


class InMemoryDailyLimitStore:
    def get(self, uid: str, action: str) -> int:
        today = _today_jst()
        return _in_memory.get(uid, {}).get(today, {}).get(action, 0)

    def increment(self, uid: str, action: str, delta: int = 1) -> int:
        today = _today_jst()
        _in_memory.setdefault(uid, {}).setdefault(today, {})
        _in_memory[uid][today][action] = _in_memory[uid][today].get(action, 0) + delta
        return _in_memory[uid][today][action]

    def reset_all(self) -> None:
        _in_memory.clear()


# ---------------------------------------------------------------------------
# Firestore実装（本番用）
# ---------------------------------------------------------------------------

class FirestoreDailyLimitStore:
    def __init__(self) -> None:
        from google.cloud import firestore
        self._db = firestore.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT")
        )

    def _doc_ref(self, uid: str):
        today = _today_jst()
        return (
            self._db.collection("users")
            .document(uid)
            .collection("daily_usage")
            .document(today)
        )

    def get(self, uid: str, action: str) -> int:
        doc = self._doc_ref(uid).get()
        if not doc.exists:
            return 0
        return int((doc.to_dict() or {}).get(action, 0))

    def increment(self, uid: str, action: str, delta: int = 1) -> int:
        from google.cloud import firestore
        ref = self._doc_ref(uid)
        ref.set({action: firestore.Increment(delta)}, merge=True)
        doc = ref.get()
        return int((doc.to_dict() or {}).get(action, 0))


# ---------------------------------------------------------------------------
# ファクトリ
# ---------------------------------------------------------------------------

_store: InMemoryDailyLimitStore | FirestoreDailyLimitStore | None = None


def _get_store() -> InMemoryDailyLimitStore | FirestoreDailyLimitStore:
    global _store
    if _store is not None:
        return _store
    if (
        "PYTEST_CURRENT_TEST" not in os.environ
        and os.environ.get("USE_FIRESTORE", "").lower() in ("1", "true", "yes")
    ):
        _store = FirestoreDailyLimitStore()
    else:
        _store = InMemoryDailyLimitStore()
    return _store


def check_and_increment(uid: str, action: str, delta: int = 1) -> UsageStatus:
    """
    現在の使用量を確認し、上限以下であればインクリメントして allowed=True を返す。
    上限に達している場合はインクリメントせず allowed=False を返す。
    """
    store = _get_store()
    limit = DAILY_LIMITS[action]
    current = store.get(uid, action)
    if current >= limit:
        return UsageStatus(
            allowed=False,
            current=current,
            limit=limit,
            reset_at_jst=_reset_label(),
        )
    new_val = store.increment(uid, action, delta)
    return UsageStatus(
        allowed=True,
        current=new_val,
        limit=limit,
        reset_at_jst=_reset_label(),
    )


def get_status(uid: str, action: str) -> UsageStatus:
    """インクリメントせず現在の使用量だけ返す。"""
    store = _get_store()
    limit = DAILY_LIMITS[action]
    current = store.get(uid, action)
    return UsageStatus(
        allowed=current < limit,
        current=current,
        limit=limit,
        reset_at_jst=_reset_label(),
    )


def reset_for_test() -> None:
    """テスト用: インメモリストアをリセットする。"""
    store = _get_store()
    if isinstance(store, InMemoryDailyLimitStore):
        store.reset_all()
