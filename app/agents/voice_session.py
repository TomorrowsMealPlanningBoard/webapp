"""
Voice Cooking Session Agent — Gemini Live API による調理中の音声インタラクション（Issue #39）。

設計方針（SPEC.md §1 Tier2 ④ / §7 に準拠）:
- 手が離せない調理中の音声相談（「次どうする？」「玉ねぎ切らした、代わりある？」）に
  リアルタイムで応答し、必要であれば献立コンテキストを踏まえた代替食材を提案する。
- Gemini Live API はモデル対応が限定される（2026-07時点で汎用の `gemini-3.1-flash-lite`
  は Live 未対応）。そのため本モジュールは Live 対応モデル
  （`GEMINI_LIVE_MODEL` 環境変数。デフォルト: `gemini-3.1-flash-live-preview`）を
  明示的に使用する。理由は PR 本文に記載する。
- 代替食材提案は「関数呼び出し（function calling）」として実装し、実処理では
  既存の Recipe Reviewer Agent（#30）の決定的フィルタ（`check_recipe` /
  `ReviewProfile`）をそのまま再利用する。新たな安全チェックを作らない。
- Live API への接続確立・送受信が失敗した場合は例外を握り、呼び出し側
  （app/main.py の WebSocket ハンドラ）が通常のテキストフローにフォールバック
  できるよう `VoiceSessionUnavailableError` のみを送出する
  （「音声機能が未対応環境でもコア献立提案が動作すること」というACに対応）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from opentelemetry import trace

from ..prompt_loader import load_prompt
from ..schemas import MealPlan, Recipe
from .reviewer import ReviewProfile, check_recipe

logger = logging.getLogger("tomorrows_meal.voice_session")
_tracer = trace.get_tracer("tomorrows_meal.voice_session")

_PROMPT_NAME = "voice_cooking_assistant"

# Gemini Live API は汎用モデル（gemini-3.1-flash-lite 等）を現時点でサポートしない。
# Live 対応が明示されているモデル世代を優先し、環境変数で上書き可能にする。
# ここでは `gemini-3.1-flash-live-preview`（Live API対応モデル）を明示的に採用する
# （指定理由はPR本文に記載）。
_DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"

SUGGEST_SUBSTITUTE_FUNCTION_NAME = "suggest_substitute_ingredient"


class VoiceSessionUnavailableError(Exception):
    """
    Gemini Live API のセッション確立・送受信に失敗したことを表す例外。

    呼び出し側（app/main.py）はこの例外を捕捉して、音声機能が使えない環境でも
    コア献立提案フロー（/api/suggest, /api/propose）は影響を受けずに動作を続ける
    フォールバック設計にすること。
    """


def get_live_model_name() -> str:
    return os.getenv("GEMINI_LIVE_MODEL", _DEFAULT_LIVE_MODEL)


@dataclass
class MealPlanContext:
    """音声セッションに渡す「現在の献立コンテキスト」。"""

    meal_plan: Optional[MealPlan] = None
    review_profile: ReviewProfile = field(default_factory=ReviewProfile)

    def current_recipes(self) -> list[Recipe]:
        if self.meal_plan is None:
            return []
        return [self.meal_plan.breakfast, self.meal_plan.lunch, self.meal_plan.dinner]

    def find_recipe_by_id(self, recipe_id: Optional[str]) -> Optional[Recipe]:
        recipes = self.current_recipes()
        if not recipes:
            return None
        if recipe_id is None:
            return recipes[0]
        for r in recipes:
            if r.id == recipe_id:
                return r
        return recipes[0]


@dataclass
class SubstituteSuggestion:
    """代替食材提案の結果。層1フィルタ通過済みの候補のみを含む。"""

    original_ingredient: str
    candidates: list[str]
    rejected_candidates: list[tuple[str, list[str]]] = field(default_factory=list)
    blocked: bool = False
    message: str = ""


# ある食材が切れた場合の一般的な代替候補（決定的なルールベースの候補プール）。
# ※ ここで挙げるのは「候補」であり、最終的な安全判定は必ず check_recipe（層1）を通す。
_SUBSTITUTE_CANDIDATES: dict[str, list[str]] = {
    "玉ねぎ": ["長ねぎ", "エシャロット", "生姜"],
    "にんにく": ["にんにくチューブ", "生姜", "にんにくパウダー"],
    "牛乳": ["豆乳", "アーモンドミルク", "水"],
    "バター": ["マーガリン", "サラダ油", "オリーブオイル"],
    "醤油": ["味噌", "ポン酢", "ウスターソース"],
    "小麦粉": ["片栗粉", "米粉", "コーンスターチ"],
    "卵": ["豆腐", "マヨネーズ", "バナナ"],
    "砂糖": ["みりん", "はちみつ", "メープルシロップ"],
}


def suggest_substitute_ingredient(
    missing_ingredient: str,
    context: MealPlanContext,
    recipe_id: Optional[str] = None,
) -> SubstituteSuggestion:
    """
    代替食材を提案する関数呼び出しツールの実処理。

    層1（アレルギー・禁止食材・調理器具）のハード制約は、既存の
    Recipe Reviewer Agent（#30）の `check_recipe` / `ReviewProfile` を
    そのまま再利用して検査する。新たな安全チェックロジックは作らない。

    候補が現在のレシピの材料リストに置き換わっても層1に違反しないかを、
    レシピを模した最小限の Recipe オブジェクトを組んで検査することで確認する。
    """
    with _tracer.start_as_current_span("suggest_substitute_ingredient") as span:
        span.set_attribute("missing_ingredient", missing_ingredient)

        base_recipe = context.find_recipe_by_id(recipe_id)
        raw_candidates = _SUBSTITUTE_CANDIDATES.get(missing_ingredient, [])

        if not raw_candidates:
            span.set_attribute("candidate_count", 0)
            return SubstituteSuggestion(
                original_ingredient=missing_ingredient,
                candidates=[],
                blocked=True,
                message=(
                    f"「{missing_ingredient}」の代替候補が見つかりませんでした。"
                    "他の食材で作れるレシピに変更することをおすすめします。"
                ),
            )

        approved: list[str] = []
        rejected: list[tuple[str, list[str]]] = []

        for candidate in raw_candidates:
            # 候補で置き換えた場合を想定した検査用レシピを組む（層1フィルタの再利用）。
            trial_ingredients = list(base_recipe.ingredients) if base_recipe else []
            trial_ingredients = [
                ing for ing in trial_ingredients if missing_ingredient not in ing
            ]
            trial_ingredients.append(candidate)

            trial_recipe = Recipe(
                id=(base_recipe.id if base_recipe else "trial"),
                title=(base_recipe.title if base_recipe else "trial"),
                emoji=(base_recipe.emoji if base_recipe else "🍽️"),
                description=(base_recipe.description if base_recipe else ""),
                cooking_time=(base_recipe.cooking_time if base_recipe else 0),
                effort_level=(base_recipe.effort_level if base_recipe else "normal"),
                servings=(base_recipe.servings if base_recipe else 1),
                tags=(list(base_recipe.tags) if base_recipe else []),
                ingredients=trial_ingredients,
                steps=(list(base_recipe.steps) if base_recipe else []),
                nutrition_note=(base_recipe.nutrition_note if base_recipe else None),
                required_tools=(list(base_recipe.required_tools) if base_recipe else []),
            )

            result = check_recipe(trial_recipe, context.review_profile)
            if result.is_valid:
                approved.append(candidate)
            else:
                reasons = [v.reason for v in result.violations]
                rejected.append((candidate, reasons))
                logger.info(
                    "substitute_candidate_rejected_by_layer1",
                    extra={"candidate": candidate, "reasons": reasons},
                )

        span.set_attribute("candidate_count", len(approved))
        span.set_attribute("rejected_count", len(rejected))

        if not approved:
            return SubstituteSuggestion(
                original_ingredient=missing_ingredient,
                candidates=[],
                rejected_candidates=rejected,
                blocked=True,
                message=(
                    f"「{missing_ingredient}」の代替候補は、アレルギーや禁止食材の"
                    "制約により提案できませんでした。別の食材を使うレシピへの"
                    "変更をおすすめします。"
                ),
            )

        return SubstituteSuggestion(
            original_ingredient=missing_ingredient,
            candidates=approved,
            rejected_candidates=rejected,
            blocked=False,
            message=f"「{missing_ingredient}」の代わりに {'、'.join(approved)} が使えます。",
        )


def _build_substitute_function_declaration() -> dict:
    """Gemini Live API に渡す関数呼び出しの宣言（Structured Outputsではなくtools）。"""
    return {
        "name": SUGGEST_SUBSTITUTE_FUNCTION_NAME,
        "description": (
            "調理中に切らしてしまった食材の代替候補を提案する。"
            "アレルギー・禁止食材・調理器具の制約を必ず考慮した候補のみを返す。"
        ),
        "parameters": {
            "type": "object",
            "required": ["missing_ingredient"],
            "properties": {
                "missing_ingredient": {
                    "type": "string",
                    "description": "切らしてしまった食材名（例: 玉ねぎ）",
                },
                "recipe_id": {
                    "type": "string",
                    "description": "対象のレシピID（省略時は現在調理中のレシピを使用）",
                },
            },
        },
    }


def _get_client():
    """Vertex AI 経由の genai.Client を構築する。既存エージェントと同じ方式。"""
    from google import genai

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise VoiceSessionUnavailableError(
            "GOOGLE_CLOUD_PROJECT 環境変数が設定されていません"
        )
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    return genai.Client(vertexai=True, project=project, location=location)


def _build_system_instruction() -> str:
    try:
        prompt = load_prompt(_PROMPT_NAME)
        return prompt.text
    except Exception as e:  # プロンプトファイルが読めない場合も音声機能全体は落とさない
        logger.warning("voice_prompt_load_failed", extra={"error": str(e)})
        return (
            "あなたは調理中のユーザーをサポートする音声アシスタントです。"
            "手が離せない状況を想定し、短く簡潔に答えてください。"
        )


class VoiceCookingSession:
    """
    Gemini Live API のセッションをラップし、テキスト化された音声質問に対して
    献立コンテキストを踏まえた応答（および代替食材の関数呼び出し）を行う。

    音声入出力そのもの（マイク録音・スピーカー再生・エンコード）はフロントエンド /
    WebSocket 層の責務とし、本クラスは「テキスト化された質問 → Gemini Live →
    応答テキスト（＋関数呼び出し結果）」のオーケストレーションに専念する。
    これにより、実際の音声デバイスが無い環境（CI・ユニットテスト）でも
    ロジックを検証できる。
    """

    def __init__(self, context: MealPlanContext):
        self.context = context

    async def ask(self, question_text: str) -> str:
        """
        テキスト化された1つの質問を Gemini Live セッションに送り、
        応答テキストを返す。

        Live API 接続・送受信に失敗した場合は VoiceSessionUnavailableError を
        送出する（呼び出し側でテキストベースのフォールバック応答に切り替える）。
        """
        with _tracer.start_as_current_span("voice_session_ask") as span:
            model_name = get_live_model_name()
            span.set_attribute("model_name", model_name)
            span.set_attribute("question_length", len(question_text))

            try:
                return await self._ask_via_live_api(question_text, span)
            except VoiceSessionUnavailableError:
                raise
            except Exception as e:
                # Live API固有の例外・ネットワークエラー等はすべてここで握り、
                # 「音声機能が未対応環境でもコア献立提案が動作すること」を満たす
                # フォールバック用の共通例外に変換する。
                span.set_attribute("error", True)
                span.set_attribute("error_message", str(e))
                logger.warning(
                    "voice_live_session_failed",
                    extra={"error": str(e), "model_name": model_name},
                )
                raise VoiceSessionUnavailableError(
                    f"Gemini Live API セッションの確立/通信に失敗しました: {e}"
                ) from e

    async def _ask_via_live_api(self, question_text: str, span) -> str:
        from google.genai import types

        client = _get_client()
        model_name = get_live_model_name()
        system_instruction = _build_system_instruction()

        tool = types.Tool(
            function_declarations=[_build_substitute_function_declaration()]
        )
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.TEXT],
            system_instruction=system_instruction,
            tools=[tool],
        )

        response_chunks: list[str] = []
        function_call_count = 0

        async with client.aio.live.connect(model=model_name, config=config) as session:
            await session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=question_text)],
                ),
                turn_complete=True,
            )

            async for message in session.receive():
                tool_call = getattr(message, "tool_call", None)
                if tool_call and getattr(tool_call, "function_calls", None):
                    function_call_count += len(tool_call.function_calls)
                    responses = []
                    for fc in tool_call.function_calls:
                        result = self._dispatch_function_call(fc.name, fc.args or {})
                        responses.append(
                            types.FunctionResponse(
                                id=fc.id,
                                name=fc.name,
                                response={"result": result},
                            )
                        )
                    await session.send_tool_response(function_responses=responses)
                    continue

                server_content = getattr(message, "server_content", None)
                if server_content and getattr(server_content, "model_turn", None):
                    for part in server_content.model_turn.parts or []:
                        if getattr(part, "text", None):
                            response_chunks.append(part.text)

                if server_content and getattr(server_content, "turn_complete", False):
                    break

        span.set_attribute("function_call_count", function_call_count)
        final_text = "".join(response_chunks).strip()
        if not final_text:
            raise VoiceSessionUnavailableError("Gemini Live から空の応答が返されました")
        return final_text

    def _dispatch_function_call(self, name: str, args: dict) -> dict:
        """Gemini Live からの関数呼び出しを実処理にディスパッチする。"""
        if name == SUGGEST_SUBSTITUTE_FUNCTION_NAME:
            suggestion = suggest_substitute_ingredient(
                missing_ingredient=args.get("missing_ingredient", ""),
                context=self.context,
                recipe_id=args.get("recipe_id"),
            )
            return {
                "original_ingredient": suggestion.original_ingredient,
                "candidates": suggestion.candidates,
                "blocked": suggestion.blocked,
                "message": suggestion.message,
            }
        logger.warning("unknown_function_call", extra={"name": name})
        return {"error": f"未知の関数呼び出しです: {name}"}


async def ask_cooking_assistant(
    question_text: str,
    context: MealPlanContext,
) -> str:
    """
    音声セッションを1問1答で実行する簡易エントリポイント。
    app/main.py の WebSocket ハンドラから呼び出される。

    Live API が使えない環境（未対応リージョン・APIキー未設定・接続失敗等）では
    VoiceSessionUnavailableError を送出するので、呼び出し側で
    フォールバック応答に切り替えること。
    """
    session = VoiceCookingSession(context=context)
    return await session.ask(question_text)


def build_fallback_response(question_text: str, context: MealPlanContext) -> str:
    """
    Live API が利用できない場合のテキストベースの簡易フォールバック応答。

    音声機能が未対応環境でもコア献立提案が動作することを保証するため、
    決定的なルールベースの最小限の応答を返す（LLM呼び出しなし）。
    「代替食材」を含む質問であれば `suggest_substitute_ingredient` を
    直接呼び出して層1フィルタ済みの候補を案内する。
    """
    for ingredient in _SUBSTITUTE_CANDIDATES:
        if ingredient in question_text:
            suggestion = suggest_substitute_ingredient(ingredient, context)
            return suggestion.message

    return (
        "現在、音声アシスタントに接続できません。恐れ入りますが、"
        "アプリ画面でレシピ手順をご確認ください。"
    )
