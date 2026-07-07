"""
層1（ハード制約）・層2（構造化FB）の永続化を担う構造化データストア（Issue #76）。

設計方針（SPEC.md §3, §5.1 に準拠）:
- 層1（アレルギー・調理器具・禁止食材）は **決定的フィルタ**（if文による機械的な読み出し）
  のみで扱う。本モジュールもベクトル検索・確率的処理を一切含まない。
- 永続先は Firestore を第一候補として選定した。決め手は以下:
  - 層1/2はユーザーIDをキーとした単純なドキュメント読み書きで足り、リレーショナルな
    JOINやトランザクションの複雑さが不要（SPEC §5.1 の構成図でも「構造化DB」として
    Memory Bank/RAG不使用のデータと同列に配置）。
  - Cloud Run実行SAのIAM(ADC)だけで接続でき、AlloyDBのようなAuth Proxy/接続プールの
    自前運用が不要（SPEC §6.4）。
  - 代替候補のCloud SQLは「AlloyDBの自前運用を排除する」という移行そもの目的（Epic #75）
    に反するため不採用。
- ローカル開発・テストでは `USE_FIRESTORE` 環境変数が未設定/false の場合、既存の
  SQLAlchemy(`db: Session`)経由の読み出しにフォールバックする（共存構成）。
"""
from __future__ import annotations

import os
from typing import List, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from ..models import Feedback, User


class HardConstraintsData:
    """層1の読み出し結果（プレーンなデータ保持用。Pydanticモデルへは呼び出し側で変換する）。"""

    def __init__(
        self,
        allergies: List[str],
        forbidden_ingredients: List[str],
        available_kitchen_tools: List[str],
    ) -> None:
        self.allergies = allergies
        self.forbidden_ingredients = forbidden_ingredients
        self.available_kitchen_tools = available_kitchen_tools


class StructuredFeedbackData:
    """層2の読み出し結果。"""

    def __init__(self, negative_tags: List[str], positive_tags: List[str]) -> None:
        self.negative_tags = negative_tags
        self.positive_tags = positive_tags


@runtime_checkable
class StructuredStore(Protocol):
    """
    層1/層2の読み出しを抽象化するインターフェース。
    決定的フィルタの原則を守るため、本Protocolの実装はif文による機械的な
    フィルタ・集約のみを行い、ベクトル検索・LLM呼び出しを含んではならない。
    """

    def get_hard_constraints(self, user_id: str) -> HardConstraintsData: ...

    def get_structured_feedback(self, user_id: str) -> StructuredFeedbackData: ...


class SqlAlchemyStructuredStore:
    """
    ローカル開発・テスト用の既存実装。SQLite/AlloyDB共存構成のデフォルト経路。
    従来の `ContextRetrieverAgent._get_hard_constraints` / `_get_structured_feedback`
    と同一のロジックをそのまま保持する。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_hard_constraints(self, user_id: str) -> HardConstraintsData:
        user = self.db.query(User).filter(User.uid == user_id).first()
        if user is None:
            raise ValueError(f"ユーザーが見つかりません: {user_id}")
        prefs = user.preferences or {}
        return HardConstraintsData(
            allergies=list(prefs.get("allergies") or []),
            forbidden_ingredients=list(prefs.get("dislikes") or []),
            available_kitchen_tools=list(prefs.get("kitchen_tools") or []),
        )

    def get_structured_feedback(self, user_id: str) -> StructuredFeedbackData:
        feedbacks = self.db.query(Feedback).filter(Feedback.user_id == user_id).all()

        negative_tags: set[str] = set()
        positive_tags: set[str] = set()
        for fb in feedbacks:
            if fb.feedback_type == "reject":
                negative_tags.update(fb.tags or [])
            elif fb.feedback_type == "cooked":
                positive_tags.update(fb.tags or [])

        return StructuredFeedbackData(
            negative_tags=sorted(negative_tags),
            positive_tags=sorted(positive_tags),
        )


class FirestoreStructuredStore:
    """
    本番用実装。層1/層2をFirestoreの `users/{user_id}` ドキュメント配下で管理する。

    コレクション構成:
      - `users/{user_id}` ドキュメントの `preferences` フィールド
        (allergies / dislikes / kitchen_tools) を層1として読み出す。
      - `users/{user_id}/feedbacks/{feedback_id}` サブコレクションを層2として読み出し、
        feedback_type (reject/cooked) で negative_tags / positive_tags に集約する。

    決定的フィルタの原則: 本クラスはFirestoreからのドキュメント取得とif文による
    集約のみを行う。ベクトル検索・埋め込み・LLM呼び出しは一切行わない。
    """

    def __init__(self, project: str | None = None) -> None:
        from google.cloud import firestore  # 遅延importでローカル開発時の依存を軽くする

        self._client = firestore.Client(project=project or os.environ.get("GOOGLE_CLOUD_PROJECT"))

    def get_hard_constraints(self, user_id: str) -> HardConstraintsData:
        doc = self._client.collection("users").document(user_id).get()
        if not doc.exists:
            raise ValueError(f"ユーザーが見つかりません: {user_id}")
        data = doc.to_dict() or {}
        prefs = data.get("preferences") or {}
        return HardConstraintsData(
            allergies=list(prefs.get("allergies") or []),
            forbidden_ingredients=list(prefs.get("dislikes") or []),
            available_kitchen_tools=list(prefs.get("kitchen_tools") or []),
        )

    def get_structured_feedback(self, user_id: str) -> StructuredFeedbackData:
        feedback_docs = (
            self._client.collection("users")
            .document(user_id)
            .collection("feedbacks")
            .stream()
        )

        negative_tags: set[str] = set()
        positive_tags: set[str] = set()
        for doc in feedback_docs:
            fb = doc.to_dict() or {}
            tags = fb.get("tags") or []
            if fb.get("feedback_type") == "reject":
                negative_tags.update(tags)
            elif fb.get("feedback_type") == "cooked":
                positive_tags.update(tags)

        return StructuredFeedbackData(
            negative_tags=sorted(negative_tags),
            positive_tags=sorted(positive_tags),
        )


def build_structured_store(db: Session) -> StructuredStore:
    """
    `USE_FIRESTORE` 環境変数に応じて層1/層2ストア実装を切り替えるファクトリ。
    未設定時（デフォルト）は既存のSQLite/AlloyDB共存構成を維持する。
    """
    if os.environ.get("USE_FIRESTORE", "").lower() in ("1", "true", "yes"):
        return FirestoreStructuredStore()
    return SqlAlchemyStructuredStore(db=db)
