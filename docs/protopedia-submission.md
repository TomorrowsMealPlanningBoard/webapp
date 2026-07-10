# Proto Pedia 作品登録テキスト一式（コピペ用）

> このファイルは protopedia.net の作品登録フォームに**そのまま貼れる**完成テキスト集です。
> 各セクション見出しがフォームの入力項目に対応します。`<!-- TODO -->` の箇所だけ埋めてください。
> 根拠: [PITCH.md](../PITCH.md) / [SPEC.md](../SPEC.md) / [README.md](../README.md) / `docs/hackathon-overview/rules.md`

---

## 0. 作品ステータス（必須）

```
開発中（本番デプロイ済み・動作可能）
```
> 任意ステータス。実機が動くので「開発中」または「完成」を選択。登録後の変更も可能。

---

## 1. 作品タイトル（必須）

**推奨（これを使う）:**
```
TomorrowsMeal（トゥモローズミール） — あなた専用に"育つ"献立AIエージェント
```

**別案:**
- `TomorrowsMeal — 冷蔵庫を撮るだけ。4体のAIエージェントが今日の献立を考える`
- `育てるAI献立エージェント TomorrowsMeal 〜DevOpsで賢くなり続ける〜`
- `TomorrowsMeal — "名もなき家事"を溶かす、自己改善する献立AI`

---

## 2. 概要（必須）

```
「今日何作ろう？」——共働き世帯を毎日悩ませるこの"名もなき家事"を、AIエージェントに丸ごと委ねます。

TomorrowsMeal は、冷蔵庫の写真・調理時間・その日の気分を送るだけで、4体のADKマルチエージェントが協調し「在庫認識 → 献立3案生成 → 安全監査（アレルギー・禁止食材・器具の決定的チェック）」を経て、あなた専用の朝・昼・晩の献立を提案するAIエージェントです。

特徴は、作って終わりにしない"保守AI"であること。ユーザーのフィードバックから好みを学ぶ「製品の学習ループ」と、GitHub Actions × LLM-as-judge でエージェント自身のプロンプトを継続改善する「DevOpsループ」——2つのループを備え、使うほど・運用するほど賢くなり続けます。食品ロス削減率・栄養達成率・献立決定時間を実測ダッシュボードで可視化。まさに "DevOps × AI Agent" を体現する作品です。
```

---

## 3. 動画（必須 / YouTube・Vimeo URL）

```
<!-- TODO: 撮影したデモ動画の YouTube または Vimeo URL をここに貼る -->
```
> デモ構成案（1〜3分）: ①冷蔵庫写真アップ → ②「AIが献立を考えています…」ローディング → ③朝昼晩3案カード表示 → ④「不採用」ボタンでのFB → ⑤調理中の音声インタラクション（Gemini Live）→ ⑥アウトカム・ダッシュボード。

---

## 4. システム構成（必須 / アーキテクチャ図アップ＋技術補足）

> **図のアップロード必須**: `docs/architecture.md` の Mermaid をPNG化してアップロード（手順は `docs/architecture-notes.md`）。図A（ランタイム＝ループA）と図B（DevOps＝ループB）の2枚を推奨。

**技術的補足（そのまま貼る）:**
```
■ 実行基盤
バックエンドは Cloud Run（Python / FastAPI）の単一コンテナに集約。ADKの4エージェント（Context Retriever / Vision Analyzer / Recipe Generator / Recipe Reviewer）は別サービスに分割せず、同一プロセス内のモジュールとして同居させています。これは「生成 → 監査 → 差し戻しループ」がエージェント間で高頻度に往復するため、マイクロサービス化するとCloud Run間のHTTPS通信遅延がLLM推論時間に重畳し実用速度に達しないこと、状態管理が複雑化すること、コールドスタントのコスト増を避けるための意図的な設計判断です。フロントは静的配信のためCloud Runは不要（1コンテナ構成）。

■ AI / エージェント
LLMは Gemini（Gemini Enterprise Agent Platform＝旧Vertex AI 経由）を使用。冷蔵庫の在庫認識は Gemini Vision の Structured Outputs で食材・量・鮮度をJSON構造化。オーケストレーションは Google ADK（Agents Development Kit）で、Reviewer Agent がアレルギー・除外タグ・未所持調理器具を検出したら Generator に理由付きで差し戻す ADK Loop 制御を実装。調理中の音声相談は Gemini Live API（native-audio モデル）で実現しています。

■ データ設計（3層の意図的分離）
データ特性ごとに処理方式を分離しました。層1（アレルギー・禁止食材・調理器具）は Firestore ＋ if文による決定的フィルタで、ベクトル検索の確率的挙動による事故を構造的に排除。層2（学習した嗜好ルール・不採用タグ）も Firestore の構造化メタデータ。層3（自由記述FBからの好み学習）は Agent Platform Memory Bank（フルマネージド長期記憶）で、DB自前運用を排除。この「確率的な記憶（層3）と決定的な安全ガードレール（層1）の意図的な分離」自体が本作品の設計主張です。

■ DevOps / 可観測性
CI/CD は GitHub Actions（ユニットテスト / Docker ビルド / LLM-as-judge eval / 改善PR自動起票 / Cloud Run 自動デプロイ / Terraform plan・apply）。可観測性は OpenTelemetry → Cloud Trace で各エージェントフェーズを計装。インフラは Terraform で宣言的に管理（Cloud Run / Artifact Registry / Firestore / Workload Identity Federation / IAM）。

なぜこの構成か: 「AIエージェントが安定して動く基盤（回帰eval・トレース）」と「エージェント自身を継続改善するパイプライン」を、汎用性とA2UI描画の自由度が高い Cloud Run に集約することで、テーマ "DevOps × AI Agent" を性能・コスト・開発速度のすべてで両立させています。
```

**使用GCPプロダクト一覧:**
- Cloud Run（バックエンド実行基盤 / 単一コンテナ）
- Gemini Enterprise Agent Platform（旧 Vertex AI）経由の Gemini（生成・Vision・Structured Outputs）
- Gemini Live API（調理中の音声インタラクション / native-audio モデル）
- Google ADK（Agents Development Kit / 4エージェント協調・Loop制御）
- Agent Platform Memory Bank（層3 好み学習 / フルマネージド長期記憶）
- Firestore（層1/層2/層3' の構造化データストア）
- Cloud Trace（OpenTelemetry / 可観測性）
- Artifact Registry / Workload Identity Federation / IAM / Secret Manager

---

## 5. 開発素材（必須 / 使用した開発ツール）

```
【クラウド / AI】
- Google Cloud Run（バックエンド実行基盤）
- Gemini Enterprise Agent Platform（旧 Vertex AI）
- Gemini API（Gemini 3 世代 Flash / Vision / Structured Outputs）
- Gemini Live API（調理中の音声インタラクション）
- Google ADK（Agents Development Kit / マルチエージェント）
- Agent Platform Memory Bank（長期記憶 / 好み学習）
- Cloud Firestore（構造化データストア）
- Cloud Trace（OpenTelemetry による可観測性）
- Artifact Registry / Workload Identity Federation / IAM / Secret Manager

【バックエンド】
- Python 3 / FastAPI / Uvicorn
- google-adk / google-genai / google-cloud-aiplatform / google-cloud-firestore
- BeautifulSoup4（外部レシピソースのスクレイピング）
- PyJWT（認証）/ slowapi（レート制限）

【フロントエンド】
- Tailwind CSS / daisyUI（emerald テーマ・モバイルファースト）
- A2UI（Generative UI / インタラクティブなレシピカード・スマートチップ）

【DevOps / IaC】
- Terraform（GCP リソースを宣言的に管理）
- GitHub Actions（CI / LLM-as-judge eval / 改善PR自動起票 / 自動デプロイ / terraform plan・apply）
- uv（Python パッケージ・環境管理）
- Docker
- pytest（ユニット / 統合 / E2E テスト）
- Slack Webhook（デプロイ・eval 結果通知）
```

---

## 6. タグ（必須 / findy_hackathon を必ず含む）

```
findy_hackathon
AIエージェント
マルチエージェント
献立
食品ロス
Gemini
CloudRun
GoogleCloud
ADK
DevOps
LLMOps
マルチモーダル
Firestore
栄養管理
```
> 1つ目の `findy_hackathon` は必須。以降は関連タグ（Proto Pediaのタグ欄に1つずつ追加）。

---

## 7. ストーリー（必須 / 3部構成）

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
① 解決したい課題とその背景
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

「今日、何作ろう？」

この一言に、共働き世帯は毎日消耗しています。仕事を終え、冷蔵庫を開け、残りの食材を睨みながら、家族の好き嫌い・アレルギー・栄養バランス・調理時間をすべて頭の中で計算して献立を決める——これは料理そのものより重い、典型的な「名もなき家事」です。誰にも評価されず、しかし毎日3回、逃れられない認知負荷。

同時に、使い切れなかった食材は静かに廃棄され、食品ロスを生みます。栄養バランスの管理は専門知識を持つ人だけの特権になりがちで、高齢者や単身者ほど後回しになります。

私たちが解きたいのは、この3つが絡み合った課題です。
・共働き世帯の「献立検討の認知負荷（名もなき家事）」の削減
・冷蔵庫の余り食材を使い切る「食品ロス削減」
・専門知識なしで栄養バランスが整う「栄養管理の民主化」

レシピ検索サービスは既にたくさんあります。しかし「検索」は、結局ユーザー自身が条件を言語化し、選び、判断する労働を残します。私たちが目指したのは、検索ではなく"委ねられる"体験。意図を汲んで選択肢を出し、最終判断だけ人に返す——Augmentation over Automation の思想です。


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
② 想定する利用ユーザー（ペルソナ）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【メインペルソナ】35歳・共働き・2児の母（父）
・平日は毎日「帰宅後30分で夕食を作る」プレッシャーと戦っている。
・上の子は卵アレルギー、下の子はナスが苦手。この制約は"絶対に外せない"。
・献立を考える時間そのものが苦痛で、結局いつも同じメニューに偏る。
・冷蔵庫に半端に残った食材を、罪悪感を抱えながら捨てている。
→ TomorrowsMeal は冷蔵庫を撮るだけで、アレルギーと苦手食材を機械的に除外した、在庫を使い切る3案を返す。決めるだけでいい。

【サブペルソナ】一人暮らしの高齢者 / 単身者
・栄養が偏りがちだが、栄養学の知識はない。凝った料理もしたくない。
→ 過去のFBから「手軽さ」を学習し、栄養達成率を可視化しながら無理なく整える。

このペルソナ設計の肝は「絶対に外せない制約（アレルギー）」と「揺らいでいい好み（気分・嗜好）」を、システムが構造的に区別している点にあります。


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
③ プロダクトの特徴 — なぜ "DevOps × AI Agent" に最も忠実か
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▼ 特徴1：AIエージェントである"必然性" — 4エージェント協調＋生成監査ループ
単一の巨大プロンプトではなく、役割特化した4体のADKエージェントが協調します。
・Context Retriever … 層別データストアからプロファイル・制約・好みを収集
・Vision Analyzer  … 冷蔵庫写真を Gemini Vision で構造化認識（食材・量・鮮度）
・Recipe Generator … 気分・調理時間を統合し朝昼晩3案を生成
・Recipe Reviewer  … アレルギー・除外タグ・未所持調理器具を厳格監査し、
                     違反があれば理由付きで Generator へ差し戻す（ADK Loop）
この「生成 → 監査 → 差し戻し」の自律ループこそ、単なる1回のLLM呼び出しでは成立しない"エージェントである必然性"です。冷蔵庫写真という曖昧・マルチモーダルな入力から意図を汲む点も、エージェントならではの価値です。

▼ 特徴2：2つのループ — これが "DevOps × AI Agent" の核心
・ループA（製品のML学習ループ／燃料）
  ユーザーの「不採用」ボタンや星評価・スマートチップから好みを構造化データとして回収し、次回提案に反映するデータフライホイール。
・ループB（DevOpsループ／駆動輪）
  集約されたFBをもとに、エージェント自身のプロンプト/ロジックを継続改善するライフサイクル。GitHub Actions が定期的に過去提案を LLM-as-judge で回帰評価し、品質スコアが下がるとプロンプト修正案を含む改善PRを自動起票（＋Slack通知）。人間のレビュー（Human-in-the-loop）を経てマージ → Cloud Run へ自動デプロイ → Cloud Trace で監視。
ループAだけでは"賢い機能"に過ぎません。ループBがあって初めて「エージェントを運用し、育て、品質を担保し続ける」——真の DevOps for AI Agent になります。私たちは"生成AI"ではなく、作って終わりにしない"保守AI"を作りました。

▼ 特徴3：3層データ設計 — 決定的フィルタと確率的記憶の意図的分離
すべてを無差別にベクトル化しません。データ特性ごとに処理を分けています。
・層1（アレルギー・禁止食材・器具）… if文による決定的フィルタ。ベクトル検索は"絶対に"使わない。確率的挙動によるアレルギー食材の見落とし事故を構造的に排除。
・層2（学習した嗜好ルール・不採用タグ）… 構造化メタデータ。透明・監査可能。
・層3（自由記述FBの好み学習）… Agent Platform Memory Bank（確率的な長期記憶）。
「確率的な記憶（層3）」と「決定的な安全ガードレール（層1）」を意図的に分離すること自体が、私たちの設計上の主張です。命に関わる制約に確率を持ち込まない——これはAIエージェントの信頼性設計そのものです。

▼ 特徴4：Human-in-the-loop
提案は必ず「AIの提案 → ユーザーの選択・微修正 → 学習」の形をとり、完全自動化しません。DevOpsループの改善PRも自動マージせず、必ず人がレビューします。AIが選択肢を広げ、最終判断は人が握る設計です。

▼ 特徴5：実測アウトカム（Output でなく Outcome / Impact）
効果を"作った機能の数"ではなく実測値で示します。食品ロス削減率（食材使い切り率）・栄養目標達成率・献立決定時間の短縮を、Four Keys 的な健康指標として「献立版ダッシュボード」に可視化しています。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TomorrowsMeal は、マルチモーダル入力を汲む自律エージェント（＝AIエージェントが価値の中心）でありながら、そのエージェント自身を継続改善する DevOps パイプラインを内蔵しています。「AIを作る」で終わらず「AIを運用し、育て続ける」。これが、本ハッカソンのテーマ "DevOps × AI Agent" に私たちが最も忠実であろうとした答えです。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 8. 関連URL（任意）

```
GitHub リポジトリ: https://github.com/TomorrowsMealPlanningBoard/webapp
デプロイ済みアプリ: https://tomorrows-meal-webapp-td7ugk7iva-an.a.run.app
```

---

## 9. メンバー登録（任意）

```
<!-- TODO: チームメンバーがいれば Proto Pedia のユーザー名で追加 -->
```

---

## 10. 登録手順メモ（登録者向け・フォームには貼らない）

- [ ] **作品ステータス**: 「開発中」または「完成」を選択（後から変更可）。
- [ ] **画像（最大5枚まで）** 推奨セット:
  1. `screenshot_02_main.png`（メイン画面）
  2. `screenshot_03_suggest_result.png`（献立3案の提案結果）
  3. `docs/architecture.md` の図A（ランタイム/ループA）をPNG化したもの
  4. `docs/architecture.md` の図B（DevOps/ループB）をPNG化したもの
  5. `dashboard-with-data.png` または `profile-v3.png`（アウトカム・ダッシュボード / プロフィール）
- [ ] **動画（必須）**: デモ動画を撮影し YouTube/Vimeo にアップ → セクション3のURLを差し込む。**動画URLが空だと登録できない**ので最優先。
- [ ] **システム構成（必須）**: アーキテクチャ図PNGのアップロードが必須。`docs/architecture-notes.md` の手順で Mermaid → PNG 化。技術補足はセクション4を貼る。
- [ ] **タグ**: `findy_hackathon` を必ず1つ目に入れる（これが無いと審査対象外）。
- [ ] **ストーリー**: セクション7を丸ごと貼る。
- [ ] 下書き保存でなく **「公開」状態**にする。
- [ ] 登録後、作品URLを控えて **作品提出フォーム（Google Form）** に「GitHub URL / デプロイURL / Proto Pedia作品URL」の3点を提出（提出期限に注意）。
```
```
