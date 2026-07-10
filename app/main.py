import asyncio
import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from . import metrics as metrics_module
from .agents import recipe_generator as recipe_generator_module
from .agents import source_extractor as source_extractor_module
from .agents import vision_analyzer
from .agents import voice_session as voice_session_module
from .agents.context_retriever import ContextRetrieverAgent
from .agents.memory_bank_client import build_vector_search_client
from .agents.notification import (
    NOTIFY_BEFORE_MINUTES,
)
from .agents.notification import (
    build_notification_payload as notification_build_payload,
)
from .agents.notification import (
    get_next_schedule as notification_get_next_schedule,
)
from .agents.orchestrator import MealOrchestrator
from .agents.proactive import get_proactive_suggestions
from .agents.reviewer import ReviewProfile
from .agents.source_scraper import SourceScrapeError, scrape_source
from .agents.voice_session import MealPlanContext, VoiceSessionUnavailableError
from .auth import (
    GOOGLE_CLIENT_ID,
    create_access_token,
    get_current_user,
    get_current_user_from_token,
    get_rate_limit_key,
    verify_google_id_token,
)
from .daily_limit import check_and_increment, get_status
from .firestore_store import (
    FeedbackDoc,
    MealProposalDoc,
    RecipeSourceDoc,
    UserDoc,
    create_user,
    get_or_create_notification_settings,
    get_meal_proposals_since,
    get_user,
    save_feedback,
    save_meal_proposals,
    save_recipe_source,
    update_notification_settings,
    update_user,
)
from .mock_recipes import MOCK_RECIPES
from .schemas import (
    FeedbackRequest,
    FeedbackResponse,
    IngredientItem,
    MealPlan,
    MealProposalItem,
    MetricsResponse,
    NotificationPayload,
    NotificationScheduleItem,
    NotificationScheduleResponse,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    NotificationTriggerResponse,
    ProactiveSuggestionItem,
    ProactiveSuggestionResponse,
    RecentProposalsResponse,
    SourceRequest,
    SourceResponse,
    SuggestRequest,
    SuggestResponse,
    GoogleAuthRequest,
    Token,
    UserProfileUpdate,
    UserResponse,
    VisionResponse,
)

logger = logging.getLogger("tomorrows_meal.suggestion_log")


def _setup_cloud_trace() -> None:
    if "PYTEST_CURRENT_TEST" in os.environ:
        return

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")

    if project:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            exporter = CloudTraceSpanExporter(project_id=project)
            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
        except Exception as exc:
            logging.getLogger("tomorrows_meal").warning(
                "cloud_trace_setup_failed", extra={"error": str(exc)}
            )
    else:
        try:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            exporter = ConsoleSpanExporter()
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
        except Exception as exc:
            logging.getLogger("tomorrows_meal").warning(
                "cloud_trace_local_setup_failed", extra={"error": str(exc)}
            )


_setup_cloud_trace()

app = FastAPI(title="TomorrowsMeal API")

limiter = Limiter(key_func=get_rate_limit_key, enabled="PYTEST_CURRENT_TEST" not in os.environ)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "detail": "リクエストが多すぎます。しばらく待ってから再度お試しください。"
        },
    )


static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/auth/google", response_model=Token)
def google_login(request_body: GoogleAuthRequest):
    idinfo = verify_google_id_token(request_body.id_token)

    google_sub = idinfo["sub"]
    email = idinfo.get("email", "")
    display_name = idinfo.get("name") or email.split("@")[0]

    user = get_user(google_sub)
    if user is None:
        user = create_user(
            uid=google_sub,
            email=email,
            display_name=display_name,
            preferences={
                "allergies": [],
                "dislikes": [],
                "goal": "diet",
                "kitchen_tools": [
                    "knife_board",
                    "peeler",
                    "grater",
                    "bowl_colander",
                    "measuring_tools",
                    "kitchen_scissors",
                ],
            },
        )

    access_token = create_access_token(data={"sub": user.uid})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/api/auth/config")
def get_auth_config():
    return {"google_client_id": GOOGLE_CLIENT_ID or ""}


@app.get("/api/profile", response_model=UserResponse)
def get_profile(current_user: UserDoc = Depends(get_current_user)):
    return current_user


@app.put("/api/profile", response_model=UserResponse)
def update_profile(
    profile_data: UserProfileUpdate,
    current_user: UserDoc = Depends(get_current_user),
):
    display_name = profile_data.display_name if profile_data.display_name is not None else None
    preferences = profile_data.preferences.model_dump() if profile_data.preferences is not None else None
    updated = update_user(current_user.uid, display_name=display_name, preferences=preferences)
    return updated


# ==========================================
# 献立提案API
# ==========================================

def _get_recently_proposed_titles(user_id: str) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    proposals = get_meal_proposals_since(user_id, cutoff)
    return {p.recipe_title for p in proposals}


def _save_proposals(user_id: str, recipe_id_titles: list[tuple[str, str]]) -> None:
    proposals = [
        MealProposalDoc({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "recipe_id": recipe_id,
            "recipe_title": recipe_title,
            "proposed_at": datetime.now(timezone.utc),
        })
        for recipe_id, recipe_title in recipe_id_titles
    ]
    save_meal_proposals(user_id, proposals)


def _suggest_mock_fallback(req: SuggestRequest, current_user: UserDoc) -> SuggestResponse:
    recently_proposed_titles = _get_recently_proposed_titles(current_user.uid)

    time_limit = req.cooking_time
    candidates = [
        r for r in MOCK_RECIPES
        if time_limit >= 999 or r["cooking_time"] <= time_limit
    ]
    if len(candidates) < 2:
        candidates = list(MOCK_RECIPES)

    non_duplicate_candidates = [
        r for r in candidates
        if r["title"] not in recently_proposed_titles
    ]
    if non_duplicate_candidates:
        candidates = non_duplicate_candidates

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

    _save_proposals(current_user.uid, [(r["id"], r["title"]) for r in selected])

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


def _build_suggest_response(req: SuggestRequest, current_user: UserDoc) -> SuggestResponse:
    context_agent = ContextRetrieverAgent()
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
        return _suggest_mock_fallback(req, current_user)

    try:
        recipes, llm_message = recipe_generator_module.generate_recipes(req, context)

        from .prompt_loader import load_prompt
        try:
            prompt_info = load_prompt("suggest")
            prompt_version = prompt_info.version
        except Exception:
            prompt_version = "unknown"

        logger.info(
            "recipe suggestion generated",
            extra={
                "user_id": current_user.uid,
                "prompt_name": "suggest",
                "prompt_version": prompt_version,
                "recipe_count": len(recipes),
                "model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            },
        )

        _save_proposals(current_user.uid, [(r.id, r.title) for r in recipes])

        return SuggestResponse(recipes=recipes, message=llm_message)

    except RuntimeError as e:
        logger.warning(f"Recipe Generator Agent の呼び出しに失敗しました（モックフォールバック）: {e}")
        return _suggest_mock_fallback(req, current_user)
    except Exception as e:
        logger.warning(f"予期せぬエラーが発生しました（モックフォールバック）: {e}")
        return _suggest_mock_fallback(req, current_user)


@app.post("/api/suggest", response_model=SuggestResponse)
@limiter.limit("5/minute")
def suggest_recipes(
    request: Request,
    req: SuggestRequest,
    current_user: UserDoc = Depends(get_current_user),
):
    return _build_suggest_response(req, current_user)


@app.post("/api/suggest/a2ui")
@limiter.limit("5/minute")
def suggest_recipes_a2ui(
    request: Request,
    req: SuggestRequest,
    current_user: UserDoc = Depends(get_current_user),
):
    from fastapi.responses import StreamingResponse
    from .a2ui import A2UI_MIME_TYPE, build_suggest_a2ui_stream

    suggest_response = _build_suggest_response(req, current_user)
    stream = build_suggest_a2ui_stream(suggest_response.recipes, suggest_response.message)
    return StreamingResponse(stream, media_type=A2UI_MIME_TYPE)


@app.get("/api/proposals/recent", response_model=RecentProposalsResponse)
def get_recent_proposals(current_user: UserDoc = Depends(get_current_user)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    proposals = get_meal_proposals_since(current_user.uid, cutoff)
    proposals.sort(key=lambda p: p.proposed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return RecentProposalsResponse(
        proposals=[
            MealProposalItem(
                id=p.id,
                recipe_id=p.recipe_id,
                recipe_title=p.recipe_title,
                proposed_at=p.proposed_at,
            )
            for p in proposals
        ]
    )


# ==========================================
# Vision API
# ==========================================

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@app.post("/api/vision", response_model=VisionResponse)
async def analyze_fridge_image(
    file: UploadFile = File(...),
    current_user: UserDoc = Depends(get_current_user),
):
    usage = check_and_increment(current_user.uid, "vision")
    if not usage.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"本日の冷蔵庫解析上限（{usage.limit}回）に達しました。",
                "remaining": 0,
                "limit": usage.limit,
                "reset_at": usage.reset_at_jst,
            },
        )

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
# ダッシュボードAPI
# ==========================================

@app.get("/api/metrics", response_model=MetricsResponse)
def get_metrics(current_user: UserDoc = Depends(get_current_user)):
    data = metrics_module.build_metrics_response(current_user.uid)
    return MetricsResponse(**data)


# ==========================================
# フィードバックAPI
# ==========================================

VALID_FEEDBACK_TYPES = {"reject", "cooked"}


def extract_feature_tags(recipe_id: str, fallback_tags: list[str]) -> list[str]:
    recipe = next((r for r in MOCK_RECIPES if r["id"] == recipe_id), None)
    if recipe and recipe.get("tags"):
        return [f"#{tag}" for tag in recipe["tags"]]
    return [f"#{tag}" for tag in fallback_tags]


async def _generate_memories_for_feedback(user_id: str, comment: str) -> None:
    try:
        client = build_vector_search_client()
        if hasattr(client, "generate_memories"):
            await client.generate_memories(user_id=user_id, texts=[comment])
    except Exception:
        logging.getLogger("tomorrows_meal.main").exception(
            "Memory Bankへの自由記述FB投入に失敗しました (user_id=%s)", user_id
        )


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(
    req: FeedbackRequest,
    background_tasks: BackgroundTasks,
    current_user: UserDoc = Depends(get_current_user),
):
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

    fb = FeedbackDoc({
        "id": str(uuid.uuid4()),
        "user_id": current_user.uid,
        "recipe_id": req.recipe_id,
        "recipe_title": req.recipe_title,
        "feedback_type": req.feedback_type,
        "tags": tags,
        "rating": req.rating,
        "comment": req.comment,
        "nutrition_goal_met": None,
        "created_at": datetime.now(timezone.utc),
    })
    save_feedback(fb)

    if req.feedback_type == "cooked" and req.comment and req.comment.strip():
        background_tasks.add_task(
            _generate_memories_for_feedback, current_user.uid, req.comment.strip()
        )

    return FeedbackResponse(
        id=fb.id,
        recipe_id=fb.recipe_id,
        feedback_type=fb.feedback_type,
        tags=fb.tags or [],
        rating=fb.rating,
        comment=fb.comment,
        created_at=fb.created_at,
    )


# ==========================================
# レシピソースAPI
# ==========================================

@app.post("/api/sources", response_model=SourceResponse)
@limiter.limit("5/minute")
def add_recipe_source(
    request: Request,
    req: SourceRequest,
    current_user: UserDoc = Depends(get_current_user),
):
    try:
        scraped = scrape_source(req.url)
    except SourceScrapeError as e:
        logger.warning(f"レシピソースのスクレイピングに失敗しました: {e}")
        raise HTTPException(status_code=422, detail=f"URLの取得に失敗しました: {e}") from e

    try:
        profile = source_extractor_module.extract_profile(scraped)
    except (RuntimeError, ValueError) as e:
        logger.warning(f"レシピソースのLLM抽出に失敗しました: {e}")
        raise HTTPException(status_code=422, detail=f"レシピソースの解析に失敗しました: {e}") from e

    summary_text = profile.to_snippet_text(scraped.title)

    src = RecipeSourceDoc({
        "id": str(uuid.uuid4()),
        "user_id": current_user.uid,
        "url": req.url,
        "source_type": scraped.source_type,
        "title": scraped.title,
        "extracted_summary": profile.model_dump(),
        "summary_text": summary_text,
        "tags": profile.tags,
        "status": "completed",
        "created_at": datetime.now(timezone.utc),
    })
    save_recipe_source(src)

    return SourceResponse(
        id=src.id,
        url=src.url,
        source_type=src.source_type,
        title=src.title,
        seasoning_tendency=profile.seasoning_tendency,
        favorite_ingredient_combos=profile.favorite_ingredient_combos,
        cooking_style=profile.cooking_style,
        tags=profile.tags,
        created_at=src.created_at,
    )


# ==========================================
# Propose API
# ==========================================

ALLOWED_MIME_TYPES_PROPOSE = {"image/jpeg", "image/png", "image/webp"}


@app.post("/api/propose", response_model=SuggestResponse)
@limiter.limit("3/minute")
async def propose_meal(
    request: Request,
    cooking_time: int = Form(30),
    effort_level: str = Form("normal"),
    mood_tags: str = Form("[]"),
    mood_freetext: str = Form(""),
    ingredients: str = Form("[]"),
    file: Optional[UploadFile] = File(None),
    current_user: UserDoc = Depends(get_current_user),
):
    try:
        tags: list[str] = json.loads(mood_tags)
    except (json.JSONDecodeError, ValueError):
        tags = []

    # 冷蔵庫タブで認識済みの食材（Vision結果）を引き継ぐ。画像を再送しなくても
    # Reviewer（監査）に食材が届くようにするため。画像が同時に送られた場合は
    # Orchestrator 側で Vision を再実行し、そちらの結果を優先する。
    recognized_ingredients: list[IngredientItem] = []
    try:
        raw_ingredients = json.loads(ingredients)
        if isinstance(raw_ingredients, list):
            recognized_ingredients = [
                IngredientItem.model_validate(item) for item in raw_ingredients
            ]
    except (json.JSONDecodeError, ValueError, TypeError):
        recognized_ingredients = []

    req = SuggestRequest(
        cooking_time=cooking_time,
        effort_level=effort_level,
        mood_tags=tags,
        mood_freetext=mood_freetext,
        ingredients=recognized_ingredients,
    )

    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None
    if file is not None:
        if file.content_type not in ALLOWED_MIME_TYPES_PROPOSE:
            raise HTTPException(
                status_code=400,
                detail=f"サポートされていない画像形式です: {file.content_type}",
            )
        image_bytes = await file.read()
        image_mime = file.content_type

    usage = check_and_increment(current_user.uid, "propose")
    if not usage.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"本日の献立提案上限（{usage.limit}回）に達しました。",
                "remaining": 0,
                "limit": usage.limit,
                "reset_at": usage.reset_at_jst,
            },
        )

    orchestrator = MealOrchestrator()
    try:
        result = await orchestrator.run(
            user_id=current_user.uid,
            req=req,
            image_bytes=image_bytes,
            image_mime_type=image_mime,
        )
    except Exception as e:
        logger.error(f"Orchestrator error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"提案の生成に失敗しました: {e}")

    logger.info(
        "orchestrator_propose_completed",
        extra={
            "user_id": current_user.uid,
            "phase_durations_ms": result.phase_durations_ms,
            "reviewer_retries": result.reviewer_retries,
            "vision_skipped": result.vision_skipped,
        },
    )

    mp = result.meal_plan
    recipes = [mp.breakfast, mp.lunch, mp.dinner]
    return SuggestResponse(recipes=recipes, message=result.message, meal_plan=mp)


# ==========================================
# 能動提案API
# ==========================================

@app.get("/api/proactive", response_model=ProactiveSuggestionResponse)
def get_proactive(current_user: UserDoc = Depends(get_current_user)):
    suggestions = get_proactive_suggestions(user=current_user)

    return ProactiveSuggestionResponse(
        suggestions=[
            ProactiveSuggestionItem(
                trigger_type=s.trigger_type,
                suggest_request=s.suggest_request,
                reason=s.reason,
                urgency=s.urgency,
            )
            for s in suggestions
        ]
    )


# ==========================================
# 通知設定API
# ==========================================

@app.get("/api/notifications/settings", response_model=NotificationSettingsResponse)
def get_notification_settings(current_user: UserDoc = Depends(get_current_user)):
    settings = get_or_create_notification_settings(current_user.uid)
    return settings


@app.put("/api/notifications/settings", response_model=NotificationSettingsResponse)
def update_notification_settings_endpoint(
    req: NotificationSettingsUpdate,
    current_user: UserDoc = Depends(get_current_user),
):
    kwargs = {}
    if req.enabled is not None:
        kwargs["enabled"] = req.enabled
    if req.breakfast_time is not None:
        kwargs["breakfast_time"] = req.breakfast_time
    if req.lunch_time is not None:
        kwargs["lunch_time"] = req.lunch_time
    if req.dinner_time is not None:
        kwargs["dinner_time"] = req.dinner_time

    settings = update_notification_settings(current_user.uid, **kwargs)
    return settings


@app.get("/api/notifications/schedule", response_model=NotificationScheduleResponse)
def get_notification_schedule(current_user: UserDoc = Depends(get_current_user)):
    settings = get_or_create_notification_settings(current_user.uid)

    if not settings.enabled:
        return NotificationScheduleResponse(schedule=[], notify_before_minutes=NOTIFY_BEFORE_MINUTES)

    schedule_items = notification_get_next_schedule(
        breakfast_time=settings.breakfast_time,
        lunch_time=settings.lunch_time,
        dinner_time=settings.dinner_time,
        notify_before_minutes=NOTIFY_BEFORE_MINUTES,
    )

    return NotificationScheduleResponse(
        schedule=[
            NotificationScheduleItem(
                meal_type=item.meal_type,
                notify_at=item.notify_at,
                meal_time=item.meal_time,
            )
            for item in schedule_items
        ],
        notify_before_minutes=NOTIFY_BEFORE_MINUTES,
    )


@app.post("/api/notifications/trigger", response_model=NotificationTriggerResponse)
def trigger_notification(
    meal_type: str,
    recipe_name: str,
    current_user: UserDoc = Depends(get_current_user),
):
    settings = get_or_create_notification_settings(current_user.uid)

    if not settings.enabled:
        return NotificationTriggerResponse(
            triggered=False,
            payload=None,
            message="通知が無効になっています。設定から有効にしてください。",
        )

    payload = notification_build_payload(meal_type=meal_type, recipe_name=recipe_name)

    return NotificationTriggerResponse(
        triggered=True,
        payload=NotificationPayload(
            meal_type=payload.meal_type,
            recipe_name=payload.recipe_name,
            title=payload.title,
            body=payload.body,
            deeplink_url=payload.deeplink_url,
        ),
        message=f"{payload.title}",
    )


# ==========================================
# 音声インタラクションAPI
# ==========================================

@app.websocket("/api/voice/session")
async def voice_session_ws(websocket: WebSocket, token: str = ""):
    await websocket.accept()

    current_user = get_current_user_from_token(token) if token else None
    if current_user is None:
        await websocket.close(code=1008, reason="認証に失敗しました")
        return

    voice_status = get_status(current_user.uid, "voice_seconds")
    if not voice_status.allowed:
        await websocket.send_json({
            "type": "daily_limit",
            "message": f"本日の音声利用上限（{voice_status.limit}秒）に達しました。{voice_status.reset_at_jst}にリセットされます。",
            "reset_at": voice_status.reset_at_jst,
        })
        await websocket.close(code=1008, reason="daily_limit_exceeded")
        return

    try:
        start_message = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1003, reason="開始メッセージの受信に失敗しました")
        return

    if start_message.get("type") != "start":
        await websocket.close(code=1003, reason="最初のメッセージは type=start である必要があります")
        return

    meal_plan_data = start_message.get("meal_plan")
    meal_plan = MealPlan(**meal_plan_data) if meal_plan_data else None
    recipe_id = start_message.get("recipe_id")

    context_agent = ContextRetrieverAgent()
    try:
        retrieved_context = await context_agent.retrieve(user_id=current_user.uid)
        review_profile = ReviewProfile(
            allergies=retrieved_context.hard_constraints.allergies,
            negative_tags=retrieved_context.structured_feedback.negative_tags,
            kitchen_tools=retrieved_context.hard_constraints.available_kitchen_tools,
        )
    except Exception as e:
        logger.warning(f"音声API: Context Retriever の呼び出しに失敗しました（既定プロファイルにフォールバック）: {e}")
        review_profile = ReviewProfile()

    voice_context = MealPlanContext(meal_plan=meal_plan, review_profile=review_profile)

    import time as _time
    _session_start = _time.monotonic()
    _remaining_seconds = voice_status.limit - voice_status.current
    _voice_limit_exceeded = False

    async def _client_audio_stream():
        nonlocal _voice_limit_exceeded
        while True:
            elapsed = _time.monotonic() - _session_start
            if elapsed >= _remaining_seconds:
                _voice_limit_exceeded = True
                return
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            if "bytes" in message and message["bytes"] is not None:
                yield message["bytes"]
                continue
            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except (TypeError, ValueError):
                    continue
                if payload.get("type") == "stop":
                    return

    session = voice_session_module.VoiceCookingSession(context=voice_context, recipe_id=recipe_id)
    try:
        async for event in session.run(_client_audio_stream()):
            if event.type == "audio" and event.audio_data:
                await websocket.send_bytes(event.audio_data)
            elif event.type == "function_call":
                await websocket.send_json({"type": "function_call", "message": event.text})
            elif event.type == "turn_complete":
                await websocket.send_json({"type": "turn_complete"})
    except VoiceSessionUnavailableError as e:
        logger.warning(f"Gemini Live セッションが利用できないためフォールバックします: {e}")
        await websocket.send_json(
            {"type": "fallback", "message": voice_session_module.FALLBACK_MESSAGE}
        )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"音声セッションで予期せぬエラーが発生しました（フォールバック）: {e}")
        try:
            await websocket.send_json(
                {"type": "fallback", "message": voice_session_module.FALLBACK_MESSAGE}
            )
        except Exception:
            pass
    finally:
        elapsed_sec = int(_time.monotonic() - _session_start)
        if elapsed_sec > 0:
            check_and_increment(current_user.uid, "voice_seconds", delta=elapsed_sec)

        if _voice_limit_exceeded:
            try:
                await websocket.send_json({
                    "type": "daily_limit",
                    "message": f"本日の音声利用上限（{voice_status.limit}秒）に達したため、セッションを終了しました。{voice_status.reset_at_jst}にリセットされます。",
                    "reset_at": voice_status.reset_at_jst,
                })
            except Exception:
                pass

        try:
            await websocket.close()
        except Exception:
            pass
