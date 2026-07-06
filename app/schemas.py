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
# 献立提案API用スキーマ
# ==========================================

class SuggestRequest(BaseModel):
    cooking_time: int = 30          # 分（999 = 無制限）
    effort_level: str = "normal"    # easy / normal / hard
    mood_tags: List[str] = Field(default_factory=list)  # 選択されたムードチップ
    mood_freetext: str = ""         # フリーテキスト
    ingredients: List[IngredientItem] = Field(default_factory=list)  # 冷蔵庫認識済み食材（Vision結果）。未認識時は空リストで後方互換。

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
    required_tools: List[str] = Field(default_factory=list)  # 調理に必要な器具（例: "オーブン"）

class MealItem(Recipe):
    """
    1食分（朝・昼・夜のいずれか）を表す。Recipe を継承し meal_type フィールドを追加する。
    Recipe Generator Agent が Structured Outputs で返す型として使用する。
    """
    meal_type: str = "dinner"  # breakfast / lunch / dinner


class MealPlan(BaseModel):
    """朝・昼・夜の3食セットを表す。"""
    breakfast: MealItem
    lunch: MealItem
    dinner: MealItem


class SuggestResponse(BaseModel):
    recipes: List[Recipe]           # 1食分の3候補レシピ（/api/suggest）、または #31 Orchestrator が返す3食
    message: str                    # AIからのひとことメッセージ
    meal_plan: Optional[MealPlan] = None  # #31 /api/propose が使用する朝昼夜プラン（/api/suggest では None）


# ==========================================
# アウトカム・ダッシュボードAPI用スキーマ（Issue #37）
# ==========================================

class MetricScalar(BaseModel):
    """単一指標（食品ロス削減率・栄養目標達成率・所要時間など）を表す共通の形。"""
    has_data: bool
    value: Optional[float] = None
    unit: str
    sample_size: int
    description: str


class QualityScorePoint(BaseModel):
    evaluated_at: Optional[str] = None
    score: float
    eval_version: Optional[str] = None
    subject_id: Optional[str] = None


class QualityScoreTrend(BaseModel):
    has_data: bool
    points: List[QualityScorePoint] = Field(default_factory=list)
    average: Optional[float] = None
    unit: str
    sample_size: int
    description: str


class MetricsResponse(BaseModel):
    food_waste_reduction_rate: MetricScalar
    nutrition_goal_achievement_rate: MetricScalar
    decision_time: MetricScalar
    cooking_time: MetricScalar
    quality_score_trend: QualityScoreTrend


# ==========================================
# フィードバックAPI用スキーマ（Issue #23 / SPEC §5.3）
# ==========================================

# ==========================================
# 提案履歴API用スキーマ（Issue #24）
# ==========================================

class MealProposalItem(BaseModel):
    """直近の提案レコードを表すスキーマ。"""
    id: str
    recipe_id: str
    recipe_title: str
    proposed_at: datetime

    class Config:
        from_attributes = True


class RecentProposalsResponse(BaseModel):
    """GET /api/proposals/recent のレスポンス。"""
    proposals: List[MealProposalItem]


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


# ==========================================
# 能動提案API用スキーマ（Issue #40 / Epic 6-3）
# ==========================================

class SuggestRequestSchema(BaseModel):
    """ProactiveSuggestion 内の SuggestRequest を JSON シリアライズするためのスキーマ。"""
    cooking_time: int = 30
    effort_level: str = "normal"
    mood_tags: List[str] = Field(default_factory=list)
    mood_freetext: str = ""
    ingredients: List["IngredientItem"] = Field(default_factory=list)


class ProactiveSuggestionItem(BaseModel):
    """
    単一の能動提案を表すスキーマ。

    - trigger_type: 提案のトリガー種別（"expiring" | "nutrition" | "calendar"）
    - suggest_request: ユーザーが承認した際にオーケストレーターへ渡せる SuggestRequest
    - reason: Human-in-the-loop のための提案理由説明文
    - urgency: 緊急度（"high" | "medium" | "low"）
    """
    trigger_type: str
    suggest_request: SuggestRequest
    reason: str
    urgency: str


class ProactiveSuggestionResponse(BaseModel):
    """
    GET /api/proactive のレスポンス。

    提案がある場合は suggestions リストに格納して返す。
    提案がない場合は空リストを返す。
    提案は Human-in-the-loop 前提であり、自動実行は行わない。
    """
    suggestions: List[ProactiveSuggestionItem] = Field(default_factory=list)


# ==========================================
# 音声インタラクションAPI用スキーマ（Issue #39 / Gemini Live）
# ==========================================

class VoiceAskRequest(BaseModel):
    """
    調理中の音声質問（テキスト化済み）を送るリクエスト。

    音声のエンコード/デコード自体はフロントエンド（ブラウザ）側の責務とし、
    バックエンドは STT 済みのテキストを受け取る想定（Gemini Live 側で
    音声入出力を担う場合は、フロントエンドが直接 WebSocket を張ることもできるが、
    その場合も本APIと同じ `MealPlan` コンテキストの受け渡し方式を使う）。
    """
    question_text: str
    meal_plan: Optional[MealPlan] = None
    recipe_id: Optional[str] = None


class VoiceAskResponse(BaseModel):
    """調理中の音声質問への応答。"""
    answer_text: str
    used_fallback: bool = False  # True: Live API未対応/失敗によりフォールバック応答を返した
