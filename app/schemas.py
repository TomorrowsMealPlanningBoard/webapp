from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class UserPreferences(BaseModel):
    allergies: List[str] = Field(default_factory=list)
    dislikes: List[str] = Field(default_factory=list)
    goal: str = "other"  # e.g., diet, bulk, maintain, none
    kitchen_tools: List[str] = Field(default_factory=list)


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    preferences: Optional[UserPreferences] = None


class UserResponse(BaseModel):
    uid: str
    email: str
    display_name: Optional[str] = None
    preferences: Optional[UserPreferences] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# 認証用スキーマ
class UserRegister(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str


# ==========================================
# 献立提案API用スキーマ
# ==========================================

class SuggestRequest(BaseModel):
    cooking_time: int = 30          # 分（999 = 無制限）
    effort_level: str = "normal"    # easy / normal / hard
    mood_tags: List[str] = Field(default_factory=list)  # 選択されたムードチップ
    mood_freetext: str = ""         # フリーテキスト

class RecipeStep(BaseModel):
    step: int
    description: str

class Recipe(BaseModel):
    id: str
    title: str
    emoji: str
    description: str
    cooking_time: int               # 調理時間（分）
    effort_level: str               # easy / normal / hard
    servings: int                   # 人数
    tags: List[str]
    ingredients: List[str]          # 材料リスト（"食材 量" 形式）
    steps: List[RecipeStep]         # 手順
    nutrition_note: Optional[str] = None  # 栄養メモ

class SuggestResponse(BaseModel):
    recipes: List[Recipe]
    message: str                    # AIからのひとことメッセージ


# ==========================================
# Vision API用スキーマ
# ==========================================

class IngredientItem(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit: str = ""
    freshness: str = "unknown"

class VisionResponse(BaseModel):
    ingredients: List[IngredientItem]
