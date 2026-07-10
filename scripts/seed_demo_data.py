"""
seed_demo_data.py — デモ撮影用シードデータの冪等投入スクリプト

デモ動画（提出用）で「育つAI（好み学習）」と「実測アウトカム・ダッシュボード」を
説得力をもって見せるため、デモ用アカウントに一貫性のあるペルソナのデータを事前投入する。
空の状態ではダッシュボードが「-」表示になり、好み学習も履歴ゼロで見せ場が死ぬ。

ペルソナ（デモ用固定ユーザー `demo_user`）:
  - 共働き・小学生の子ども1人。平日は時短志向、和食寄り。
  - 卵アレルギー（層1: 決定的フィルタで必ず除外）。
  - 苦手: パクチー、レバー。
  - 調理器具: フライパン、電子レンジ、炊飯器、鍋（オーブンなし ← 監査Loopの説得力に使える）。
  - 好み学習の「効き」を見せるための一貫した傾向:
      * 揚げ物を繰り返し不採用（reject: #揚げ物 / #油っこい）
      * 甘めの和食・煮物を高評価（cooked rating=5, #ちょうど良い甘さ）
      * さっぱり・時短が刺さる

投入するデータ（どの指標/機能を埋めるか → firestore_store.py / metrics.py 参照）:
  - users/{uid}                        … プロフィール（層1: allergies/dislikes/kitchen_tools, goal）
  - users/{uid}/meal_proposals         … 直近提案履歴（GET /api/proposals/recent が履歴を返す）
  - users/{uid}/feedbacks              … 不採用タグ・星評価・スマートチップ・自由記述FB・nutrition_goal_met
                                         （栄養目標達成率 / 層2構造化FB / 層3自由記述の元）
  - users/{uid}/recipe_sources         … お気に入りレシピソース（層3': 外部URL由来の構造化データ）
  - users/{uid}/meal_histories         … 食材使い切り(was_expiring) / 決定時間 / 実測調理時間
  - quality_score_logs                 … 提案品質スコア推移（LLM-as-judge のグラフ）

冪等性:
  - すべて固定 ID で set() する（再実行しても重複しない・上書きされる）。
  - 相対日付は「実行時刻から N 日前」で生成するため、いつ実行しても「直近」の履歴になる。

接続先の切替（安全側デフォルト=ローカル/エミュレータ）:
  - デフォルトは FIRESTORE_EMULATOR_HOST が設定されていなければ localhost:8080 のエミュレータへ向ける。
  - 本番の実 Firestore に投入したい場合は明示的に `--target prod` を指定する（誤爆防止）。

使い方:
  # ローカル/エミュレータ（デフォルト・安全側）
  uv run python scripts/seed_demo_data.py

  # 本番デモアカウントへ投入（明示的に）
  export GOOGLE_CLOUD_PROJECT="<project-id>"
  uv run python scripts/seed_demo_data.py --target prod --user demo_user

  # 撮影後クリーンアップ
  uv run python scripts/seed_demo_data.py --target prod --user demo_user --clean

層3 Memory Bank（日本語自由記述の好み学習）への投入はデフォルトでは行わない
（実 LLM/embedding 課金と Issue #82 検証との競合を避けるため）。
`--with-memory-bank` を明示したときのみ、`--mb-user` で指定した別スコープの user_id に投入する。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# app パッケージを import できるようにリポジトリルートをパスへ追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_USER_ID = "demo_user"
DEFAULT_MB_USER_ID = "demo_user_memorybank"  # 層3投入は別スコープに分離（#82と競合させない）
DEFAULT_EMULATOR_HOST = "localhost:8080"
DEFAULT_LOCAL_PROJECT = "tomorrows-meal-local"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(days: int, hour: int = 18, minute: int = 0) -> datetime:
    """実行時刻から days 日前の指定時刻（UTC）。相対日付なのでいつ実行しても直近になる。"""
    base = _now() - timedelta(days=days)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# ペルソナ定義（一貫性のあるシードデータ本体）
# ---------------------------------------------------------------------------

def build_profile(uid: str) -> dict:
    now = _now()
    return {
        "uid": uid,
        "email": "demo@tomorrowsmeal.example",
        "hashed_password": None,
        "display_name": "デモ 太郎",
        "preferences": {
            # 層1（決定的フィルタ）: 卵アレルギーは必ず除外される
            "allergies": ["卵"],
            "dislikes": ["パクチー", "レバー"],
            "goal": "maintain",
            # オーブンなし → オーブン料理が弾かれる監査Loopを見せられる
            "kitchen_tools": ["フライパン", "電子レンジ", "炊飯器", "鍋"],
            # 賞味期限が近い食材 → S5 の能動提案（expiring）が発火する状態にする。
            # 相対日付で3食材が3日以内に期限を迎える（うち1つは当日）ため、
            # 賞味期限優先の提案が high 緊急度（3件以上）で出る。
            "ingredients": [
                {"name": "鶏むね肉", "quantity": 1, "unit": "枚",
                 "expiry_date": _days_ago(-1).date().isoformat()},   # 明日
                {"name": "小松菜", "quantity": 1, "unit": "束",
                 "expiry_date": _days_ago(0).date().isoformat()},    # 本日
                {"name": "絹豆腐", "quantity": 1, "unit": "丁",
                 "expiry_date": _days_ago(-2).date().isoformat()},   # 明後日
            ],
        },
        "created_at": now - timedelta(days=30),
        "updated_at": now,
    }


def build_meal_proposals(uid: str) -> list[dict]:
    """直近提案履歴。GET /api/proposals/recent は 7日以内を返すため直近に収める。"""
    specs = [
        (0, 18, "recipe_007", "鮭とれんこんの甘辛照り焼き"),
        (0, 12, "recipe_011", "豚こまと白菜のさっぱり煮"),
        (1, 18, "recipe_002", "豚こまと野菜のみそ炒め"),
        (1, 7, "recipe_015", "納豆とねぎの和風チャーハン"),
        (2, 18, "recipe_001", "鶏むね肉のさっぱりレモン炒め"),
        (3, 18, "recipe_009", "かぼちゃとひき肉の甘煮"),
        (4, 18, "recipe_004", "ぶり大根"),
        (5, 18, "recipe_012", "厚揚げと小松菜の煮びたし"),
    ]
    out = []
    for i, (days, hour, rid, title) in enumerate(specs):
        out.append({
            "id": f"{uid}_proposal_{i:03d}",
            "user_id": uid,
            "recipe_id": rid,
            "recipe_title": title,
            "proposed_at": _days_ago(days, hour),
        })
    return out


def build_feedbacks(uid: str) -> list[dict]:
    """
    フィードバック履歴。好み学習が「効いている」と分かる一貫した傾向:
      - 揚げ物を繰り返し不採用（reject）
      - 甘めの和食・煮物を高評価（cooked rating=5）
    栄養目標達成率は nutrition_goal_met（True/False）で算出される。
    """
    fbs: list[dict] = []

    def add(idx: int, days: int, ftype: str, rid: str, title: str,
            tags: list[str], rating=None, comment=None, nutrition=None):
        fbs.append({
            "id": f"{uid}_fb_{idx:03d}",
            "user_id": uid,
            "recipe_id": rid,
            "recipe_title": title,
            "feedback_type": ftype,
            "tags": tags,
            "rating": rating,
            "comment": comment,
            "nutrition_goal_met": nutrition,
            "created_at": _days_ago(days, 20),
        })

    # --- 揚げ物の不採用を繰り返す（負の傾向の一貫性）---
    add(0, 1, "reject", "recipe_020", "鶏のから揚げ", ["#揚げ物", "#油っこい"])
    add(1, 4, "reject", "recipe_021", "とんかつ", ["#揚げ物", "#重い"])
    add(2, 8, "reject", "recipe_022", "アジフライ", ["#揚げ物", "#後片付けが大変"])
    # 手間がかかるものも不採用（時短志向）
    add(3, 6, "reject", "recipe_023", "本格ビーフシチュー", ["#時間がかかる", "#手間"])

    # --- 甘めの和食・煮物を高評価（正の傾向の一貫性・自由記述は層3へ）---
    add(4, 2, "cooked", "recipe_007", "鮭とれんこんの甘辛照り焼き",
        ["#ちょうど良い甘さ", "#ごはんが進む", "#また作りたい"],
        rating=5, comment="甘辛い味付けが家族にちょうど良かった。子どもがよく食べた。",
        nutrition=True)
    add(5, 3, "cooked", "recipe_009", "かぼちゃとひき肉の甘煮",
        ["#ちょうど良い甘さ", "#簡単", "#時短"],
        rating=5, comment="優しい甘さで大満足。電子レンジでできて平日にありがたい。",
        nutrition=True)
    add(6, 5, "cooked", "recipe_011", "豚こまと白菜のさっぱり煮",
        ["#さっぱり", "#簡単", "#野菜たっぷり"],
        rating=4, comment="さっぱりして食べやすい。もう少し甘めでも良いかも。",
        nutrition=True)
    add(7, 7, "cooked", "recipe_012", "厚揚げと小松菜の煮びたし",
        ["#やさしい味", "#ヘルシー"],
        rating=4, comment="上品な和食の味。作り置きにも良さそう。",
        nutrition=True)
    add(8, 9, "cooked", "recipe_002", "豚こまと野菜のみそ炒め",
        ["#ごはんが進む", "#がっつり"],
        rating=3, comment=None, nutrition=False)  # 栄養未達の実例（達成率を100%未満に）
    add(9, 11, "cooked", "recipe_001", "鶏むね肉のさっぱりレモン炒め",
        ["#さっぱり", "#高タンパク", "#ヘルシー"],
        rating=4, comment="レモンでさっぱり。ダイエット中にちょうどいい。",
        nutrition=True)

    return fbs


def build_recipe_sources(uid: str) -> list[dict]:
    """お気に入りレシピソース（層3': 外部URL由来の構造化データ）。"""
    now = _now()
    return [
        {
            "id": f"{uid}_source_001",
            "user_id": uid,
            "url": "https://cookpad.example.com/recipe/wafu-nimono-basics",
            "source_type": "blog",
            "title": "基本の和風煮物 やさしい甘辛だれ",
            "extracted_summary": {
                "seasoning_tendency": "醤油・みりん・砂糖ベースの甘辛い和風味付け",
                "favorite_ingredient_combos": ["鮭×れんこん", "かぼちゃ×ひき肉", "厚揚げ×小松菜"],
                "cooking_style": "フライパン・鍋で短時間で仕上げる家庭的な和食",
                "tags": ["和食", "甘辛", "時短", "子ども向け"],
            },
            "summary_text": (
                "味付けの傾向: 醤油・みりん・砂糖ベースの甘辛い和風味付け\n"
                "好まれる食材の組み合わせ: 鮭×れんこん、かぼちゃ×ひき肉、厚揚げ×小松菜\n"
                "調理スタイル: フライパン・鍋で短時間で仕上げる家庭的な和食"
            ),
            "tags": ["和食", "甘辛", "時短", "子ども向け"],
            "status": "completed",
            "error_message": None,
            "created_at": now - timedelta(days=12),
        },
        {
            "id": f"{uid}_source_002",
            "user_id": uid,
            "url": "https://youtube.example.com/watch?v=jitan-wafu-15min",
            "source_type": "youtube",
            "title": "平日15分で作る さっぱり和食おかず",
            "extracted_summary": {
                "seasoning_tendency": "だし・ポン酢を効かせたさっぱり系の和風",
                "favorite_ingredient_combos": ["豚こま×白菜", "鶏むね×レモン"],
                "cooking_style": "電子レンジと1つのフライパンで完結する時短調理",
                "tags": ["時短", "さっぱり", "和食", "平日"],
            },
            "summary_text": (
                "味付けの傾向: だし・ポン酢を効かせたさっぱり系の和風\n"
                "好まれる食材の組み合わせ: 豚こま×白菜、鶏むね×レモン\n"
                "調理スタイル: 電子レンジと1つのフライパンで完結する時短調理"
            ),
            "tags": ["時短", "さっぱり", "和食", "平日"],
            "status": "completed",
            "error_message": None,
            "created_at": now - timedelta(days=6),
        },
    ]


def build_meal_histories(uid: str) -> list[dict]:
    """
    食事履歴。ダッシュボード3指標の元データ:
      - 食品ロス削減率: ingredients_used[].was_expiring
      - 献立決定時間: suggested_at → decided_at
      - 実測調理時間: cooking_started_at → cooking_completed_at
    """
    def hist(idx: int, days: int, meal_type: str, title: str,
             ingredients: list[dict],
             decision_sec: int, cooking_min: int) -> dict:
        suggested = _days_ago(days, 17, 30)
        decided = suggested + timedelta(seconds=decision_sec)
        cook_start = decided + timedelta(minutes=5)
        cook_end = cook_start + timedelta(minutes=cooking_min)
        return {
            "id": f"{uid}_history_{idx:03d}",
            "user_id": uid,
            "date": (suggested.date()).isoformat(),
            "meal_type": meal_type,
            "status": "completed",
            "recipe": {"title": title},
            "suggested_at": suggested,
            "decided_at": decided,
            "cooking_started_at": cook_start,
            "cooking_completed_at": cook_end,
            "ingredients_used": ingredients,
            "created_at": suggested,
        }

    def ing(name: str, expiring: bool) -> dict:
        return {"name": name, "was_expiring": expiring}

    histories = [
        hist(0, 2, "dinner", "鮭とれんこんの甘辛照り焼き",
             [ing("鮭", True), ing("れんこん", True), ing("醤油", False), ing("みりん", False)],
             decision_sec=42, cooking_min=18),
        hist(1, 3, "dinner", "かぼちゃとひき肉の甘煮",
             [ing("かぼちゃ", True), ing("合いびき肉", True), ing("だし", False)],
             decision_sec=35, cooking_min=15),
        hist(2, 5, "dinner", "豚こまと白菜のさっぱり煮",
             [ing("豚こま肉", True), ing("白菜", True), ing("ポン酢", False)],
             decision_sec=51, cooking_min=20),
        hist(3, 7, "dinner", "厚揚げと小松菜の煮びたし",
             [ing("厚揚げ", False), ing("小松菜", True), ing("だし", False)],
             decision_sec=28, cooking_min=12),
        hist(4, 9, "dinner", "豚こまと野菜のみそ炒め",
             [ing("豚こま肉", True), ing("キャベツ", True), ing("にんじん", False), ing("みそ", False)],
             decision_sec=60, cooking_min=20),
        hist(5, 11, "dinner", "鶏むね肉のさっぱりレモン炒め",
             [ing("鶏むね肉", True), ing("レモン", False), ing("にんにく", False)],
             decision_sec=39, cooking_min=15),
    ]
    return histories


def build_quality_score_logs(uid: str) -> list[dict]:
    """
    提案品質スコア（LLM-as-judge）の推移。徐々に上がる = 学習で改善しているストーリー。
    quality_score_logs はユーザー横断コレクション（metrics は user_id 一致 or None を集計）。
    """
    scores = [
        (13, 0.72), (11, 0.74), (9, 0.78), (7, 0.81),
        (5, 0.85), (3, 0.88), (2, 0.90), (0, 0.92),
    ]
    out = []
    for i, (days, score) in enumerate(scores):
        out.append({
            "id": f"{uid}_qscore_{i:03d}",
            "user_id": uid,
            "subject_type": "suggestion",
            "subject_id": f"{uid}_proposal_{i:03d}",
            "score": score,
            "eval_version": "demo-v1",
            "rationale": "デモ用シードデータ（好み学習により提案品質が段階的に向上）",
            "evaluated_at": _days_ago(days, 21),
        })
    return out


# 層3 Memory Bank に投入する日本語自由記述FB（オプション・別スコープ）
MEMORY_BANK_TEXTS = [
    "甘辛い和風の味付けが家族に好評。子どもがよく食べる。",
    "揚げ物は後片付けが大変で敬遠しがち。できれば避けたい。",
    "平日は電子レンジとフライパン1つで作れる時短メニューが助かる。",
    "さっぱりした和食が好み。ポン酢やレモンを使った味付けが好き。",
]


# ---------------------------------------------------------------------------
# Firestore への投入 / クリーンアップ
# ---------------------------------------------------------------------------

def _get_firestore_client():
    from google.cloud import firestore
    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))


def seed_firestore(uid: str) -> None:
    client = _get_firestore_client()
    user_ref = client.collection("users").document(uid)

    # プロフィール（層1）
    user_ref.set(build_profile(uid))
    print(f"  [users/{uid}] プロフィール投入（アレルギー=卵, オーブンなし）")

    def _set_subcollection(name: str, docs: list[dict]) -> None:
        col = user_ref.collection(name)
        batch = client.batch()
        for d in docs:
            batch.set(col.document(d["id"]), d)
        batch.commit()
        print(f"  [users/{uid}/{name}] {len(docs)} 件投入")

    _set_subcollection("meal_proposals", build_meal_proposals(uid))
    _set_subcollection("feedbacks", build_feedbacks(uid))
    _set_subcollection("recipe_sources", build_recipe_sources(uid))
    _set_subcollection("meal_histories", build_meal_histories(uid))

    # 品質スコアログ（ユーザー横断コレクション）
    qlogs = build_quality_score_logs(uid)
    qcol = client.collection("quality_score_logs")
    batch = client.batch()
    for d in qlogs:
        batch.set(qcol.document(d["id"]), d)
    batch.commit()
    print(f"  [quality_score_logs] {len(qlogs)} 件投入")


def clean_firestore(uid: str) -> None:
    client = _get_firestore_client()
    user_ref = client.collection("users").document(uid)

    for name in ["meal_proposals", "feedbacks", "recipe_sources", "meal_histories"]:
        col = user_ref.collection(name)
        n = 0
        for doc in col.stream():
            doc.reference.delete()
            n += 1
        print(f"  [users/{uid}/{name}] {n} 件削除")

    # 品質スコアログ（当該デモユーザー分のみ）
    n = 0
    for doc in client.collection("quality_score_logs").where("user_id", "==", uid).stream():
        doc.reference.delete()
        n += 1
    print(f"  [quality_score_logs] {n} 件削除（user_id={uid}）")

    user_ref.delete()
    print(f"  [users/{uid}] プロフィール削除")


def seed_memory_bank(mb_user: str) -> None:
    """層3の日本語自由記述FBを Memory Bank に投入（オプション・実 embedding 課金あり）。"""
    from app.agents.memory_bank_client import MemoryBankVectorSearchClient

    if not os.getenv("MEMORY_BANK_AGENT_ENGINE_ID"):
        print(
            "  [memory_bank] スキップ: MEMORY_BANK_AGENT_ENGINE_ID が未設定です。\n"
            "  （embedding_model=gemini-embedding-001 の Agent Engine を用意してください。#82参照）"
        )
        return

    client = MemoryBankVectorSearchClient()
    asyncio.run(client.generate_memories(user_id=mb_user, texts=MEMORY_BANK_TEXTS))
    print(f"  [memory_bank] {len(MEMORY_BANK_TEXTS)} 件の日本語FBを投入（user_id={mb_user}）")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def _configure_target(target: str) -> None:
    """接続先を設定。デフォルトはエミュレータ（安全側）。"""
    if target == "local":
        if not os.getenv("FIRESTORE_EMULATOR_HOST"):
            os.environ["FIRESTORE_EMULATOR_HOST"] = DEFAULT_EMULATOR_HOST
        if not os.getenv("GOOGLE_CLOUD_PROJECT"):
            os.environ["GOOGLE_CLOUD_PROJECT"] = DEFAULT_LOCAL_PROJECT
        print(
            f"[seed] 接続先: ローカル/エミュレータ "
            f"(FIRESTORE_EMULATOR_HOST={os.environ['FIRESTORE_EMULATOR_HOST']}, "
            f"project={os.environ['GOOGLE_CLOUD_PROJECT']})"
        )
    else:  # prod
        if os.getenv("FIRESTORE_EMULATOR_HOST"):
            print(
                "[seed] エラー: --target prod ですが FIRESTORE_EMULATOR_HOST が設定されています。\n"
                "       本番へ向けるにはエミュレータ変数を解除してください: unset FIRESTORE_EMULATOR_HOST",
                file=sys.stderr,
            )
            sys.exit(2)
        if not os.getenv("GOOGLE_CLOUD_PROJECT"):
            print(
                "[seed] エラー: --target prod には GOOGLE_CLOUD_PROJECT が必須です。\n"
                "       export GOOGLE_CLOUD_PROJECT=\"<project-id>\"",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"[seed] 接続先: 本番 Firestore (project={os.environ['GOOGLE_CLOUD_PROJECT']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=["local", "prod"], default="local",
                        help="投入先（デフォルト: local=エミュレータ・安全側）")
    parser.add_argument("--user", default=DEFAULT_USER_ID,
                        help=f"デモ用 user_id（デフォルト: {DEFAULT_USER_ID}）")
    parser.add_argument("--clean", action="store_true",
                        help="シードデータを削除する（撮影後クリーンアップ）")
    parser.add_argument("--with-memory-bank", action="store_true",
                        help="層3 Memory Bank にも日本語FBを投入する（実embedding課金あり・別スコープ）")
    parser.add_argument("--mb-user", default=DEFAULT_MB_USER_ID,
                        help=f"Memory Bank 投入用の別スコープ user_id（デフォルト: {DEFAULT_MB_USER_ID}）")
    args = parser.parse_args()

    _configure_target(args.target)

    if args.clean:
        print(f"[seed] クリーンアップ開始 (user={args.user})")
        clean_firestore(args.user)
        print("[seed] クリーンアップ完了")
        return

    print(f"[seed] シードデータ投入開始 (user={args.user})")
    seed_firestore(args.user)

    if args.with_memory_bank:
        print(f"[seed] Memory Bank 投入開始 (mb_user={args.mb_user})")
        seed_memory_bank(args.mb_user)

    print("[seed] 完了。GET /api/metrics と GET /api/proposals/recent が実数値/履歴を返します。")


if __name__ == "__main__":
    main()
