from sqlalchemy import Column, String, DateTime, Numeric, Date, ForeignKey, JSON, Integer
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
