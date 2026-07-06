"""
Voice Cooking Session Agent — Gemini Live API による調理中の音声インタラクション（Issue #39）。

設計方針（SPEC.md §1 Tier2 ④ / §7 に準拠）:
- 手が離せない調理中、ユーザーは「会話を開始」するだけで音声のみで相談できる
  （テキスト入力は行わない）。ブラウザのマイク音声を PCM チャンクのまま
  WebSocket 経由でバックエンドに送り、バックエンドが Gemini Live API との
  ストリーミングセッションを中継する「ブリッジ」構成を取る
  （認証情報をフロントに渡さずに済むため）。
- 献立コンテキスト（現在調理中のレシピ）と層1ハード制約（アレルギー・禁止食材・
  調理器具）は、セッション開始時に system_instruction へ注入し、会話の間
  Gemini Live 側に保持させる。ユーザーは質問ごとにコンテキストを渡す必要がない。
- Gemini Live API はモデル対応が限定される。本プロジェクトは Vertex AI 経由
  （ADC認証、GCPプロジェクト課金）で Gemini を呼び出す構成のため、Vertex AI が
  Live API 用に公開している音声出力モデル
  （`GEMINI_LIVE_MODEL` 環境変数。デフォルト: `gemini-live-2.5-flash-native-audio`）を
  使用する。このモデルは音声出力必須（TEXTのみの応答は不可）なため、
  `response_modalities=[AUDIO]` + `output_audio_transcription` を指定し、
  音声波形と文字起こし（字幕表示・ログ用）を同時に受け取る。
  `gemini-3.1-flash-live-preview` は AI Studio 専用（generativelanguage API）で
  Vertex AI 経由では未提供のため採用しない（検証済み、理由は PR 本文に記載）。
- 代替食材提案は「関数呼び出し（function calling）」として実装し、実処理では
  既存の Recipe Reviewer Agent（#30）の決定的フィルタ（`check_recipe` /
  `ReviewProfile`）をそのまま再利用する。新たな安全チェックを作らない。
- Live API への接続確立・送受信が失敗した場合は例外を握り、呼び出し側
  （app/main.py の WebSocket ハンドラ）が「音声機能が未対応環境でもコア献立提案が
  動作すること」というACを満たせるよう `VoiceSessionUnavailableError` を送出する。
  呼び出し側はフォールバック通知をフロントに送り、フロントは音声UIを閉じて
  通常のレシピ画面操作を継続できる（音声機能なしでもコア機能は損なわれない）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from opentelemetry import trace

from ..prompt_loader import load_prompt
from ..schemas import MealPlan, Recipe
from .reviewer import ReviewProfile, check_recipe

logger = logging.getLogger("tomorrows_meal.voice_session")
_tracer = trace.get_tracer("tomorrows_meal.voice_session")

_PROMPT_NAME = "voice_cooking_assistant"

# Gemini Live API は汎用モデル（gemini-3.1-flash-lite 等）を現時点でサポートしない。
# 本プロジェクトは Vertex AI 経由（vertexai=True）で Gemini を呼ぶ構成のため、
# Vertex AI が Live API 用に公開しているモデルを使う。このモデルは音声出力専用
# （response_modalities に TEXT を指定するとエラーになる）。
# `gemini-3.1-flash-live-preview` は AI Studio 専用で Vertex AI 経由では未提供のため
# 採用しない（指定理由はPR本文に記載）。
_DEFAULT_LIVE_MODEL = "gemini-live-2.5-flash-native-audio"

# ブラウザの MediaRecorder / AudioWorklet から送られてくる音声チャンクの形式。
# Gemini Live API の realtime input は 16bit PCM, 16kHz, mono を要求する。
INPUT_AUDIO_MIME_TYPE = "audio/pcm;rate=16000"

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


#  Live API 用モデル（gemini-live-2.5-flash-native-audio）は `global` では
# 提供されておらず、`us-central1` でのみ動作することを実機検証済み
# （2026-07時点、GCPプロジェクト agentic-ai-495701 で確認）。他のエージェント
# （通常のテキスト生成）は `GOOGLE_CLOUD_LOCATION=global` を前提にしているため
# 環境変数を共有せず、Live API 専用のロケーションとして固定値で持つ。
_LIVE_API_LOCATION = "us-central1"


def _get_client():
    """Vertex AI 経由の genai.Client を構築する。既存エージェントと同じ方式。"""
    from google import genai

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise VoiceSessionUnavailableError(
            "GOOGLE_CLOUD_PROJECT 環境変数が設定されていません"
        )
    return genai.Client(vertexai=True, project=project, location=_LIVE_API_LOCATION)


def _build_system_instruction(context: MealPlanContext, recipe_id: Optional[str] = None) -> str:
    """
    system_instruction を構築する。プロンプトファイル本文の末尾に、
    現在調理中のレシピ（レシピ名・材料・手順）を埋め込む。

    これが無いと Gemini Live は「以下のレシピを作っています」という指示文だけを
    受け取り、実際にどのレシピかを知らないまま一般論で応答してしまう
    （層1フィルタ通過済みの代替候補は関数呼び出しで正しく渡るが、手順に関する
    質問には対応できない）。
    """
    try:
        prompt = load_prompt(_PROMPT_NAME)
        base_instruction = prompt.text
    except Exception as e:  # プロンプトファイルが読めない場合も音声機能全体は落とさない
        logger.warning("voice_prompt_load_failed", extra={"error": str(e)})
        base_instruction = (
            "あなたは調理中のユーザーをサポートする音声アシスタントです。"
            "手が離せない状況を想定し、短く簡潔に答えてください。"
        )

    recipe = context.find_recipe_by_id(recipe_id)
    if recipe is None:
        return base_instruction

    steps_text = "\n".join(f"{s.step}. {s.description}" for s in recipe.steps)
    recipe_block = (
        "\n\n## 現在ユーザーが作っているレシピ\n"
        f"料理名: {recipe.title}\n"
        f"材料: {', '.join(recipe.ingredients)}\n"
        f"手順:\n{steps_text}\n"
        "この材料・手順に基づいて回答してください。ここに無い食材や手順を答えては"
        "いけません。"
    )
    return base_instruction + recipe_block


@dataclass
class VoiceSessionEvent:
    """
    Gemini Live セッションからフロントエンド（WebSocket）へ配信する1イベント。

    `type` ごとに意味が異なる:
      - "audio": Gemini からの音声出力チャンク（`audio_data` に生PCMバイト列）
      - "function_call": 代替食材の関数呼び出し結果（`text` に案内文）
      - "turn_complete": 1ターンの応答が完了したことを示す区切り
    """

    type: str
    audio_data: Optional[bytes] = None
    text: Optional[str] = None


class VoiceCookingSession:
    """
    Gemini Live API とのストリーミングセッションをラップするブリッジ。

    ブラウザのマイク音声（PCMチャンクの非同期イテレータ）を受け取り、
    そのまま Gemini Live へリアルタイム転送しつづける一方で、Gemini からの
    音声出力・文字起こし・関数呼び出し結果を `VoiceSessionEvent` として
    非同期に yield する。音声入出力のエンコード/デコード・マイク制御・
    スピーカー再生は呼び出し側（WebSocket ハンドラ・フロントエンド）の責務。
    """

    def __init__(self, context: MealPlanContext, recipe_id: Optional[str] = None):
        self.context = context
        self.recipe_id = recipe_id

    async def run(
        self, audio_in: AsyncIterator[bytes]
    ) -> AsyncIterator[VoiceSessionEvent]:
        """
        Gemini Live とのセッションを開始し、`audio_in` から音声チャンクを
        受け取りながら Gemini からのイベントを yield し続ける。

        `audio_in` が終端（会話終了）するとセッションを閉じて終了する。
        Live API 接続・送受信に失敗した場合は VoiceSessionUnavailableError を
        送出する（呼び出し側で「音声機能が使えません」通知に切り替える）。
        """
        import asyncio

        from google.genai import types

        with _tracer.start_as_current_span("voice_session_run") as span:
            model_name = get_live_model_name()
            span.set_attribute("model_name", model_name)

            try:
                client = _get_client()
                system_instruction = _build_system_instruction(self.context, self.recipe_id)
                tool = types.Tool(
                    function_declarations=[_build_substitute_function_declaration()]
                )
                config = types.LiveConnectConfig(
                    response_modalities=[types.Modality.AUDIO],
                    system_instruction=system_instruction,
                    tools=[tool],
                )

                async with client.aio.live.connect(
                    model=model_name, config=config
                ) as session:
                    send_task = asyncio.create_task(
                        self._forward_audio_input(session, audio_in)
                    )
                    try:
                        async for event in self._receive_events(session, span, send_task):
                            yield event
                    finally:
                        send_task.cancel()
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

    async def _forward_audio_input(self, session, audio_in: AsyncIterator[bytes]) -> None:
        """フロントから届く音声チャンクをそのまま Gemini Live へ転送し続ける。"""
        from google.genai import types

        async for chunk in audio_in:
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=INPUT_AUDIO_MIME_TYPE)
            )

    async def _receive_events(self, session, span, send_task) -> AsyncIterator[VoiceSessionEvent]:
        """
        Gemini Live からのメッセージを VoiceSessionEvent に変換して yield する。

        `session.receive()` は「1つの完全なモデルターン」を返すごとに終了する
        イテレータ（google-genai SDKの仕様）。会話は複数ターンにわたって
        継続するため、`turn_complete` を受け取った後も `session.receive()` を
        呼び直し続ける必要がある。これを1回しか呼ばないと、1回目の応答後に
        ユーザーが話しかけても永遠に無反応になる（2ターン目以降が返ってこない
        バグの原因）。

        `send_task`（`audio_in` を Gemini Live へ転送し続けるタスク）が完了
        （＝ユーザーが会話を終了/切断した）したら、このループも終了する。
        次のターンの受信待ち（`receive_task`）と `send_task` の完了を
        `asyncio.wait` で競合させることで、受信側が busy-loop 化せず、
        かつ終了時に即座にループを抜けられるようにする。
        """
        import asyncio

        from google.genai import types

        function_call_count = 0

        while True:
            receive_task = asyncio.ensure_future(self._receive_one_turn(session))
            done, _ = await asyncio.wait(
                {receive_task, send_task}, return_when=asyncio.FIRST_COMPLETED
            )

            if receive_task not in done:
                # send_task が先に完了した（会話終了/切断）ので受信も打ち切る。
                receive_task.cancel()
                return

            messages = receive_task.result()
            if send_task.done() and not messages:
                # 会話終了後、受信側にも新規メッセージが来なくなった場合の保険。
                return

            for message in messages:
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
                        yield VoiceSessionEvent(type="function_call", text=result.get("message", ""))
                    await session.send_tool_response(function_responses=responses)
                    continue

                server_content = getattr(message, "server_content", None)
                if not server_content:
                    continue

                model_turn = getattr(server_content, "model_turn", None)
                if model_turn:
                    for part in model_turn.parts or []:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data and getattr(inline_data, "data", None):
                            yield VoiceSessionEvent(type="audio", audio_data=inline_data.data)

                if getattr(server_content, "turn_complete", False):
                    span.set_attribute("function_call_count", function_call_count)
                    yield VoiceSessionEvent(type="turn_complete")

    async def _receive_one_turn(self, session) -> list:
        """`session.receive()` の1ターン分のメッセージをリストにまとめて返す。"""
        return [message async for message in session.receive()]

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


FALLBACK_MESSAGE = (
    "現在、音声アシスタントに接続できません。恐れ入りますが、"
    "アプリ画面でレシピ手順をご確認ください。"
)
