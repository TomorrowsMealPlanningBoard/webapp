from sqlalchemy import Column, String, DateTime, Numeric, Date, ForeignKey, JSON, Integer, Float, Boolean
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"

    uid = Column(String(128), primary_key=True, index=True)
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    preferences = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Inventory(Base):
    __tablename__ = "inventories"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(50), nullable=False)
    expiration_date = Column(DateTime(timezone=True), nullable=True)
    image_url = Column(String, nullable=True)
    registered_via = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class MealHistory(Base):
    __tablename__ = "meal_histories"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    meal_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    recipe = Column(JSON, nullable=False)

    # --- Issue #37: アウトカム・ダッシュボード用の実測データ ---
    # 提案が表示された時刻。ここから決定までの経過時間で「献立決定時間」を算出する。
    suggested_at = Column(DateTime(timezone=True), nullable=True)
    # ユーザーがレシピを最終的に選択・確定した時刻。
    decided_at = Column(DateTime(timezone=True), nullable=True)
    # 調理を開始・完了した時刻（あれば）。実測の調理時間短縮を算出するために使用。
    cooking_started_at = Column(DateTime(timezone=True), nullable=True)
    cooking_completed_at = Column(DateTime(timezone=True), nullable=True)
    # レシピが要求する材料リストと、実際にInventoryから消費（使い切り）された材料の対応。
    # 例: [{"name": "にんじん", "used_quantity": 1, "unit": "本", "was_expiring": false}, ...]
    # 食品ロス削減率（食材使い切り率）の算出に使用する。データが無い間は空リスト。
    ingredients_used = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Feedback(Base):
    """
    レシピ提案に対するフィードバック（SPEC §5.3 / Issue #23）。

    feedback_type:
      - "reject": 提案時の「不採用（もう表示しない）」FB。tags には特徴タグ（例: #揚げ物 #豚肉）を格納。
      - "cooked": 調理後FB。rating（星1-5）＋ スマートチップで選択したtags ＋ 任意のcomment。
    """
    __tablename__ = "feedbacks"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False)
    recipe_id = Column(String(64), nullable=False)
    recipe_title = Column(String(255), nullable=True)
    feedback_type = Column(String(20), nullable=False)  # reject / cooked
    tags = Column(JSON, nullable=True)                  # 特徴タグ or スマートチップ選択タグ
    rating = Column(Integer, nullable=True)              # 1 〜 5（調理後FBのみ）
    comment = Column(String(1000), nullable=True)        # 自由記述（オプション）
    # ユーザーが自己申告した栄養目標達成度合い。#34/栄養連携実装までは未使用（Issue #37 ダッシュボード用の先行カラム）。
    nutrition_goal_met = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class QualityScoreLog(Base):
    """
    Issue #37: LLM-as-judgeによる「提案品質スコア」の時系列記録。
    LLM-as-judge eval基盤(#34)がまだ存在しないため、このテーブルは
    #34実装時に書き込まれる先行スキーマとして用意する。
    現時点ではレコードが0件でも /api/metrics が空配列を返せるようにする。
    """
    __tablename__ = "quality_score_logs"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), nullable=True)
    # 評価対象（例: プロンプトのバージョン、meal_history_id等）
    subject_type = Column(String(50), nullable=False, default="suggestion")
    subject_id = Column(String(64), nullable=True)
    # LLM-as-judgeによるスコア（0.0〜1.0 または 0〜100 など、evalの実装に合わせる）
    score = Column(Float, nullable=False)
    # 評価に使ったプロンプト/ロジックのバージョン（ループBのトレーサビリティ用）
    eval_version = Column(String(50), nullable=True)
    # 評価理由・コメント（LLM-as-judgeの出力）
    rationale = Column(String, nullable=True)
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now())
