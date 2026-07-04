from sqlalchemy import Column, String, DateTime, Numeric, Date, ForeignKey, JSON
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Feedback(Base):
    """
    層2（構造化FB）・層3（自由記述FB）のソースとなるテーブル。

    - negative_tags / positive_tags: 構造化メタデータ（層2）。
      ベクトル検索のハードフィルタ（除外/優先）として Context Retriever Agent が利用する。
    - free_text: 自由記述テキスト（層3）。将来的に埋め込みベクトル化してベクトルDBへ格納する。
      現段階（AlloyDB/pgvector 未プロビジョニング = #28）では平文で保持し、
      Context Retriever Agent 側の VectorSearchClient 抽象を通じて簡易検索する。
    """

    __tablename__ = "feedbacks"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False)
    recipe_id = Column(String(64), nullable=True)
    negative_tags = Column(JSON, nullable=False, default=list)
    positive_tags = Column(JSON, nullable=False, default=list)
    free_text = Column(String, nullable=True)
    rating = Column(String(10), nullable=True)  # 1〜5 の星評価（文字列で保持しシンプルに）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
