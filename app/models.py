from sqlalchemy import Column, String, DateTime, Numeric, Date, ForeignKey, JSON
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"

    uid = Column(String(128), primary_key=True, index=True)
    email = Column(String(255), nullable=False)
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
