from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import asyncio
import logging
import os
import uuid
import random

from .database import engine, Base, get_db
from .models import User, Feedback
from .schemas import UserProfileUpdate, UserResponse, UserRegister, Token, SuggestRequest, SuggestResponse, VisionResponse, IngredientItem, MetricsResponse, FeedbackRequest, FeedbackResponse
from .auth import get_password_hash, verify_password, create_access_token, get_current_user
from .mock_recipes import MOCK_RECIPES
from .agents import vision_analyzer
from .agents.context_retriever import ContextRetrieverAgent
from .agents import recipe_generator as recipe_generator_module
from . import metrics as metrics_module

logger = logging.getLogger("tomorrows_meal.suggestion_log")

# データベーステーブルの作成
Base.metadata.create_all(bind=engine)

app = FastAPI(title="TomorrowsMeal API")


# 初期データの作成（default_userが存在しない場合）
def init_db():
    db = next(get_db())
    try:
        default_user = db.query(User).filter(User.uid == "default_user").first()
        if not default_user:
            new_user = User(
                uid="default_user",
                email="guest@example.com",
                hashed_password=get_password_hash("password"),
                display_name="ゲストユーザー",
                preferences={
                    "allergies": [],
                    "dislikes": [],
                    "goal": "other",
                    "kitchen_tools": []
                }
            )
            db.add(new_user)
            db.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        db.close()


init_db()

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/health")
def health_check():
    return {"status": "ok"}

# アカウント作成 API（JSON形式）
@app.post("/api/auth/register", response_model=Token)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています。")

    uid = str(uuid.uuid4())
    hashed_pwd = get_password_hash(user_data.password)
    new_user = User(
        uid=uid,
        email=user_data.email,
        hashed_password=hashed_pwd,
        display_name=user_data.display_name or user_data.email.split("@")[0],
        preferences={
            "allergies": [],
            "dislikes": [],
            "goal": "none",
            "kitchen_tools": []
        }
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    access_token = create_access_token(data={"sub": new_user.uid})
    return {"access_token": access_token, "token_type": "bearer"}

# ログイン API（FastAPI標準の OAuth2PasswordRequestForm を使用）
# username フィールドにメールアドレスを入力する
@app.post("/api/auth/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # OAuth2の仕様では username フィールドを使う（ここではメールアドレスを受け取る）
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="メールアドレスまたはパスワードが正しくありません。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.uid})
    return {"access_token": access_token, "token_type": "bearer"}

# プロファイル取得API
@app.get("/api/profile", response_model=UserResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    return current_user


# プロファイル更新API
@app.put("/api/profile", response_model=UserResponse)
def update_profile(
    profile_data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if profile_data.display_name is not None:
        current_user.display_name = profile_data.display_name

    if profile_data.preferences is not None:
        current_user.preferences = profile_data.preferences.model_dump()

    db.commit()
    db.refresh(current_user)
    return current_user


# ==========================================
# 献立提案API（LLM実装 + モックフォールバック）
# ==========================================

def _suggest_mock_fallback(req: SuggestRequest) -> SuggestResponse:
    """
    LLM呼び出しが失敗した際のモックフォールバック。
    既存のMOCK_RECIPESからスコアリングして最大3件を返す。
    """
    time_limit = req.cooking_time  # 999 = 無制限
    candidates = [
        r for r in MOCK_RECIPES
        if time_limit >= 999 or r["cooking_time"] <= time_limit
    ]
    if len(candidates) < 2:
        candidates = list(MOCK_RECIPES)

    freetext = req.mood_freetext.strip()

    def score(recipe: dict) -> float:
        s = 0.0
        if recipe["effort_level"] == req.effort_level:
            s += 3.0
        for tag in req.mood_tags:
            if tag in recipe["tags"]:
                s += 2.0
        if freetext:
            searchable_text = " ".join([
                recipe["title"],
                recipe["description"],
                *recipe["tags"],
                *recipe["ingredients"],
            ])
            for keyword in ["肉", "魚", "野菜", "さっぱり", "こってり", "汁", "ご飯", "麺", "疲れ", "簡単"]:
                if keyword in freetext and keyword in searchable_text:
                    s += 1.5
        s += random.uniform(0, 1)
        return s

    candidates.sort(key=score, reverse=True)
    selected = candidates[:3]

    mood_items = [*req.mood_tags]
    if freetext:
        mood_items.append(freetext)
    mood_str = "・".join(mood_items) if mood_items else "おまかせ"
    effort_label = {"easy": "ラクチン", "normal": "普通", "hard": "本格派"}.get(req.effort_level, "普通")
    time_label = "時間無制限" if time_limit >= 999 else f"{time_limit}分以内"
    message = (
        f"🤖 【モックデータ】{time_label}・{effort_label}・{mood_str}の条件で"
        f" {len(selected)}品を提案します！（AI連携は準備中）"
    )
    return SuggestResponse(recipes=selected, message=message)


@app.post("/api/suggest", response_model=SuggestResponse)
def suggest_recipes(
    req: SuggestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    朝・昼・夜の3食献立を LLM（Gemini）で生成して返す。
    処理フロー（SPEC.md §5.2 準拠）:
      1. Context Retriever Agent でプロファイル・FB・ハード制約を取得
      2. Recipe Generator Agent で LLM に3食提案を要求
      3. レスポンスを SuggestResponse に変換して返す
    LLM 呼び出しが失敗した場合はモックにフォールバック（エラーが出ても動く状態を保つ）。
    """
    # --- Step1: Context Retriever Agent でコンテキスト取得 ---
    context_agent = ContextRetrieverAgent(db=db)
    query_text = " ".join(req.mood_tags)
    if req.mood_freetext:
        query_text = f"{query_text} {req.mood_freetext}".strip()

    try:
        context = asyncio.run(context_agent.retrieve(
            user_id=current_user.uid,
            query_text=query_text,
        ))
    except Exception as e:
        logger.warning(f"Context Retriever Agent の呼び出しに失敗しました（フォールバック）: {e}")
        return _suggest_mock_fallback(req)

    # --- Step2: Recipe Generator Agent で LLM 呼び出し ---
    try:
        meal_plan, llm_message = recipe_generator_module.generate_meal_plan(req, context)

        # meal_plan の3食を recipes リストに変換（後方互換のため維持）
        recipes = [meal_plan.breakfast, meal_plan.lunch, meal_plan.dinner]

        # 提案ログ（SPEC.md §4 ループB バージョン管理）
        from .prompt_loader import load_prompt
        try:
            prompt_info = load_prompt("suggest")
            prompt_version = prompt_info.version
        except Exception:
            prompt_version = "unknown"

        logger.info(
            "meal_plan suggestion generated",
            extra={
                "user_id": current_user.uid,
                "prompt_name": "suggest",
                "prompt_version": prompt_version,
                "meal_count": 3,
                "model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            },
        )

        return SuggestResponse(
            recipes=recipes,
            message=llm_message,
            meal_plan=meal_plan,
        )

    except RuntimeError as e:
        # LLM 呼び出し失敗（APIキー未設定・ネットワークエラー等）→ モックにフォールバック
        logger.warning(f"Recipe Generator Agent の呼び出しに失敗しました（モックフォールバック）: {e}")
        return _suggest_mock_fallback(req)
    except Exception as e:
        # その他の予期せぬエラー → モックにフォールバック
        logger.warning(f"予期せぬエラーが発生しました（モックフォールバック）: {e}")
        return _suggest_mock_fallback(req)


# ==========================================
# Vision API（冷蔵庫写真 → 食材リスト）
# ==========================================

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@app.post("/api/vision", response_model=VisionResponse)
async def analyze_fridge_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    冷蔵庫の写真をアップロードして食材リストをAIで抽出する。
    Gemini Vision（Structured Outputs）を使用する。
    """
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"サポートされていない画像形式です: {file.content_type}。JPEG / PNG / WebP を使用してください。",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="画像データが空です")

    try:
        result = vision_analyzer.analyze_image(image_bytes, file.content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 提案ログ: どのプロンプトバージョン（Gitコミットハッシュ）で生成されたかを記録する
    # （SPEC.md §4 ループB「バージョン管理」。将来的にはCloud Trace等の可観測性基盤に送る）
    logger.info(
        "vision_analysis suggestion generated",
        extra={
            "user_id": current_user.uid,
            "prompt_name": "vision_analysis",
            "prompt_version": result.prompt_version,
            "ingredient_count": len(result.ingredients),
        },
    )

    return VisionResponse(
        ingredients=[
            IngredientItem(
                name=ing.name,
                quantity=ing.quantity,
                unit=ing.unit,
                freshness=ing.freshness,
            )
            for ing in result.ingredients
        ]
    )


# ==========================================
# アウトカム・ダッシュボードAPI（Issue #37）
# ==========================================

@app.get("/api/metrics", response_model=MetricsResponse)
def get_metrics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Outcome / Impact 指標を実測値で返す。

    - 食品ロス削減率（食材使い切り率）
    - 栄養目標達成率
    - 献立決定時間 / 調理時間
    - 提案品質スコア（LLM-as-judge）の推移

    現時点では提案履歴管理(#24)・LLM-as-judge eval(#34)が未実装のため、
    データが蓄積されていない指標は has_data=False で
    「データ蓄積中」であることを正直に返す。算出ロジック自体は実データが
    揃った際にそのまま正しく機能する。
    """
    data = metrics_module.build_metrics_response(db, current_user.uid)
    return MetricsResponse(**data)


# ==========================================
# フィードバックAPI（Issue #23 / SPEC §5.3）
# ==========================================

VALID_FEEDBACK_TYPES = {"reject", "cooked"}


def extract_feature_tags(recipe_id: str, fallback_tags: list[str]) -> list[str]:
    """
    レシピの特徴タグ（例: #揚げ物 #豚肉）を抽出する。
    現状はモックレシピが持つ `tags` をそのまま特徴タグとして採用するルールベース実装。
    （将来的にAI連携レシピになった場合も、レシピ生成時にtagsを付与する設計を踏襲する）
    """
    recipe = next((r for r in MOCK_RECIPES if r["id"] == recipe_id), None)
    if recipe and recipe.get("tags"):
        return [f"#{tag}" for tag in recipe["tags"]]
    return [f"#{tag}" for tag in fallback_tags]


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(
    req: FeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    レシピ提案へのフィードバックを保存する。
      - feedback_type = "reject": 「不採用（もう表示しない）」。特徴タグを自動抽出して保存。
      - feedback_type = "cooked": 調理後の星評価（1〜5必須）＋ スマートチップタグ ＋ 任意の自由記述。
    """
    if req.feedback_type not in VALID_FEEDBACK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"feedback_type は {sorted(VALID_FEEDBACK_TYPES)} のいずれかを指定してください。",
        )

    if req.feedback_type == "cooked" and req.rating is None:
        raise HTTPException(
            status_code=400,
            detail="調理後フィードバック（cooked）には rating（1〜5）が必須です。",
        )

    if req.feedback_type == "reject":
        tags = extract_feature_tags(req.recipe_id, req.tags)
    else:
        tags = req.tags

    feedback = Feedback(
        id=str(uuid.uuid4()),
        user_id=current_user.uid,
        recipe_id=req.recipe_id,
        recipe_title=req.recipe_title,
        feedback_type=req.feedback_type,
        tags=tags,
        rating=req.rating,
        comment=req.comment,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    return FeedbackResponse(
        id=feedback.id,
        recipe_id=feedback.recipe_id,
        feedback_type=feedback.feedback_type,
        tags=feedback.tags or [],
        rating=feedback.rating,
        comment=feedback.comment,
        created_at=feedback.created_at,
    )
