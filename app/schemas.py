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


# ==========================================
# フィードバックAPI用スキーマ（Issue #23 / SPEC §5.3）
# ==========================================

class FeedbackRequest(BaseModel):
    recipe_id: str
    recipe_title: Optional[str] = None
    feedback_type: str                          # "reject" or "cooked"
    tags: List[str] = Field(default_factory=list)  # 不採用時の特徴タグ or 調理後のスマートチップ選択タグ
    rating: Optional[int] = Field(default=None, ge=1, le=5)  # 調理後の星評価（1〜5）
    comment: Optional[str] = None                # 自由記述（オプション）


class FeedbackResponse(BaseModel):
    id: str
    recipe_id: str
    feedback_type: str
    tags: List[str]
    rating: Optional[int] = None
    comment: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
