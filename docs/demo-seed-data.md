# デモ撮影用シードデータ

デモ動画（S3 好み学習ループ・S6 アウトカムダッシュボード）を説得力のある画面にするための、
デモ用アカウントへの事前投入シードデータと投入スクリプトの手引き。

- スクリプト: [`scripts/seed_demo_data.py`](../scripts/seed_demo_data.py)
- 実行: `uv run python scripts/seed_demo_data.py`
- 撮影前チェックリストは [`docs/demo-video-script.md` §4](demo-video-script.md) と対応

空アカウントのままだとダッシュボードの各指標が「-」表示になり、好み学習も履歴ゼロで
「育つAI」の見せ場が死ぬ。このスクリプトは一貫性のあるペルソナ1名分のデータを冪等投入する。

---

## 1. 何を・なぜ入れるか

### ペルソナ（デモ用固定ユーザー `demo_user`）
共働き・小学生の子ども1人。平日は時短志向で和食寄り。**卵アレルギー**。

このペルソナは意図的に「好み学習の効きが目に見える」よう傾向を一貫させている:
- **揚げ物を繰り返し不採用**（から揚げ・とんかつ・アジフライを reject）
- **甘めの和食・煮物を高評価**（甘辛照り焼き・かぼちゃ甘煮に星5）
- さっぱり・時短が刺さる

### 投入データと、埋まるダッシュボード指標・機能の対応

| Firestore の場所 | 件数 | 何のため（読み取り側） |
|---|---|---|
| `users/demo_user`（preferences） | 1 | 層1 決定的フィルタ（卵アレルギー・オーブンなし）。`structured_store.get_hard_constraints` |
| `users/demo_user/meal_proposals` | 8 | `GET /api/proposals/recent`（直近7日の提案履歴） |
| `users/demo_user/feedbacks` | 10 | 栄養目標達成率 / 層2構造化FB（不採用・スマートチップ）/ 層3自由記述の元 |
| `users/demo_user/recipe_sources` | 2 | 層3' お気に入りレシピソース（外部URL由来の構造化データ） |
| `users/demo_user/meal_histories` | 6 | 食品ロス削減率（`was_expiring`）/ 献立決定時間 / 実測調理時間 |
| `quality_score_logs`（user_id 一致） | 8 | 提案品質スコア（LLM-as-judge）の推移グラフ |

### 好み学習の「効き」がデモで見える工夫
- 不採用FBのタグを `#揚げ物` `#油っこい` `#後片付けが大変` に**繰り返し**寄せてある
  → 層2の negative_tags に一貫して現れ、「揚げ物を学習して避けている」と説明できる。
- 高評価FBを甘めの和食（`#ちょうど良い甘さ`）に寄せてある → positive_tags と自由記述FBの
  両方に「甘辛・和食好み」が現れる。層3'のレシピソース2件も同じ方向（甘辛和風・時短さっぱり）に
  揃えてあり、3つの層が同じ人物像を指す構成にしている。
- 品質スコアは 0.72 → 0.92 と時系列で**右肩上がり**にしてあり、「学習で提案品質が向上」の
  ストーリーが折れ線グラフに出る。

---

## 2. ローカル検証結果（エミュレータ相当）

Firestore クライアントをインメモリfakeに差し替えて実際の `app/metrics.py` /
`GET /api/proposals/recent` ロジックを通した結果、すべての指標が「-」でなく実数値を返すことを確認済み:

```
food_waste_reduction_rate:        50.0 %   (n=20)
nutrition_goal_achievement_rate:  83.3 %   (n=6)
decision_time:                    42.5 秒  (n=6)
cooking_time:                     1000.0 秒 (≒16.7分, n=6)
quality_score_trend:              average=0.825, points=8
proposals/recent:                 8 件
層2 negative_tags: #揚げ物 #油っこい #後片付けが大変 #手間 #時間がかかる #重い
層2 positive_tags: #ちょうど良い甘さ #さっぱり #ヘルシー #時短 #簡単 ...
```

---

## 3. 実行方法

### 3.1 ローカル / エミュレータ（デフォルト・安全側）
`--target` 省略時はローカルに向く。`FIRESTORE_EMULATOR_HOST` 未設定なら `localhost:8080` を使う。

```bash
# 事前に Firestore エミュレータを起動しておく（別ターミナル）
#   gcloud emulators firestore start --host-port=localhost:8080
uv run python scripts/seed_demo_data.py
```

### 3.2 本番デモアカウントへ投入（撮影直前に実行）
本番へ向けるときは `--target prod` を**明示**する（誤爆防止のためデフォルトにはしない）。
`FIRESTORE_EMULATOR_HOST` が残っていると安全のため中断する。

```bash
unset FIRESTORE_EMULATOR_HOST
export GOOGLE_CLOUD_PROJECT="<本番プロジェクトID>"
# ADC 認証（未ログインなら）: gcloud auth application-default login

uv run python scripts/seed_demo_data.py --target prod --user demo_user
```

投入後、撮影用アカウントで `GET /api/metrics` と `GET /api/proposals/recent` が
実数値/履歴を返すことをブラウザ（ダッシュボードタブ・提案履歴）で確認する。

> `demo_user` はデモ表示用の固定 user_id。実際の撮影アカウントの Google ログイン uid と
> 揃える必要がある場合は `--user <実uid>` を指定する（JWT の `sub` と一致させる）。

### 3.3 層3 Memory Bank（オプション・実embedding課金あり）
日本語自由記述FBの好み学習を Memory Bank 側でも見せたい場合のみ。**Issue #82 検証と競合しないよう
別スコープ user_id**（デフォルト `demo_user_memorybank`）に投入する。

```bash
export MEMORY_BANK_AGENT_ENGINE_ID="<embedding_model=gemini-embedding-001 の Agent Engine ID>"
uv run python scripts/seed_demo_data.py --target prod --with-memory-bank --mb-user demo_user_memorybank
```

---

## 4. 冪等性・安全性

- 全ドキュメントを**固定ID**で `set()` するため、**再実行しても重複せず上書き**される。
- 履歴の日付は「実行時刻から N 日前」の**相対**で生成。いつ実行しても「直近」の履歴になる。
- 既定は**ローカル/エミュレータ**に向く。本番は `--target prod` の明示が必要。
- `quality_score_logs` はユーザー横断コレクションだが、クリーンアップは `user_id == demo_user`
  のみを削除するため、他ユーザーのログを壊さない。

---

## 5. 撮影後クリーンアップ

```bash
# 本番から demo_user のシードデータを削除
unset FIRESTORE_EMULATOR_HOST
export GOOGLE_CLOUD_PROJECT="<本番プロジェクトID>"
uv run python scripts/seed_demo_data.py --target prod --user demo_user --clean
```

`--clean` は `users/demo_user` 配下のサブコレクション（meal_proposals / feedbacks /
recipe_sources / meal_histories）と `quality_score_logs`（user_id=demo_user 分）、および
プロフィール本体を削除する。Memory Bank に投入した場合は別途 Memory Bank 側で
`demo_user_memorybank` スコープの記憶を削除すること。

---

## 6. 撮影前チェックリスト（[demo-video-script.md §4](demo-video-script.md) と整合）

- [ ] `uv run python scripts/seed_demo_data.py --target prod --user <撮影uid>` を実行済み
- [ ] ダッシュボードの4指標（🥦食品ロス削減率・🥗栄養目標達成率・⏱️献立決定時間・🍳実測調理時間）が数値表示
- [ ] 提案品質スコアの推移グラフが右肩上がりの線を描く
- [ ] 提案履歴（`/api/proposals/recent`）に直近の献立が並ぶ
- [ ] プロフィールに卵アレルギー・オーブンなしが入っており、監査Loop（S2）で説明できる
- [ ] 撮影後は `--clean` でシードデータを片付ける
