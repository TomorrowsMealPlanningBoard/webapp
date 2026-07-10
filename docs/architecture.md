# TomorrowsMeal アーキテクチャ図

> ハッカソン提出（Proto Pedia「システム構成図」）用の清書図。
> 設計の根拠は [SPEC.md](../SPEC.md)（§4「2つのループ」/ §5 アーキテクチャ / §6 デプロイ構成 / §7 全体俯瞰）を参照。
> このドキュメントの Mermaid ソースを PNG 化して Proto Pedia にアップロードする（手順は [architecture-notes.md](architecture-notes.md)）。

TomorrowsMeal は「AIエージェントが価値の中心」であることを 2 つのループで表現する。
**図A（ループA / ランタイム）** はユーザー体験を駆動する ADK マルチエージェントの協調、
**図B（ループB / DevOps）** はエージェント自身を継続改善する自己改善パイプラインを示す。

---

## 図A：ランタイム・アーキテクチャ（ループA / ML学習フライホイール）

ユーザーがスマホから「冷蔵庫写真・気分・調理時間」を送ると、Cloud Run 内の **ADK Orchestrator** が
4 エージェント（Context Retriever / Vision Analyzer / Recipe Generator / Recipe Reviewer）を協調させ、
**「生成 → 監査 → 差し戻しループ」** を経て安全な献立 3 案を返す。
層1/層2（Firestore・決定的フィルタ）、層3（Memory Bank）、層3'（構造化DB）、Gemini（Vertex AI 経由）、
Gemini Live API、Health API への接続を明示している。

```mermaid
flowchart TB
    User["📱 ユーザー<br/>スマホブラウザ<br/>(静的フロント: Cloud Run から配信)"]

    subgraph CloudRun["☁️ Cloud Run（Python / FastAPI）— 1コンテナに集約"]
        direction TB
        Orch["🧭 ADK Orchestrator<br/>(Google ADK Workflow)<br/>並列制御・差し戻しループ制御"]

        subgraph Agents["ADKマルチエージェント（同一プロセス内モジュール）"]
            direction TB
            CR["1 Context Retriever Agent<br/>プロファイル・FB・好み取得<br/>ハイブリッド検索"]
            VA["2 Vision Analyzer Agent<br/>冷蔵庫画像を構造化認識<br/>(Structured Outputs)"]
            RG["3 Recipe Generator Agent<br/>レシピ3案を生成"]
            RV["4 Recipe Reviewer Agent<br/>制約監査・ガードレール"]
        end

        Proactive["⑤ Proactive Agent<br/>賞味期限/栄養/作り置きの能動提案"]
        Voice["④ Voice Session<br/>調理中の音声インタラクション"]
    end

    subgraph Data["データストア（層別に処理方式を分離）"]
        direction TB
        FS[("層1/層2: Firestore<br/>アレルギー・禁止食材・器具=決定的フィルタ<br/>不採用タグ等の構造化FB=メタデータフィルタ")]
        MB[("層3: Memory Bank<br/>(Agent Platform Memory Bank)<br/>自由記述FBからの好み学習・類似度検索")]
        SRC[("層3': 構造化DB (Firestore相当)<br/>外部レシピソース要約<br/>全件プロンプト直接注入")]
    end

    subgraph LLM["Gemini（Vertex AI / Gemini Enterprise Agent Platform 経由）"]
        direction TB
        GVision["Gemini Vision<br/>(Structured Outputs)"]
        GGen["Gemini<br/>レシピ生成"]
        GRev["Gemini<br/>制約監査"]
    end

    Live["🎙️ Gemini Live API<br/>リアルタイム音声"]
    Health["❤️ Google Health API<br/>(Fitness) 栄養データ"]
    Scraper["🌐 Web Scraper<br/>YouTube / ブログ取得"]

    %% ユーザー往復
    User -->|"① HTTPS<br/>冷蔵庫写真/気分/時間"| Orch
    Orch -->|"⑥ レシピカード / FBチップ<br/>(Generative UI / A2UI)"| User

    %% オーケストレーション
    Orch --> CR
    Orch --> VA
    Orch --> RG
    Orch --> RV
    Orch --> Proactive
    Orch --> Voice

    %% 生成→監査→差し戻しループ
    CR -->|"コンテキスト"| RG
    VA -->|"食材JSON"| RG
    RG -->|"3案"| RV
    RV -.->|"リジェクト: 理由付き差し戻し<br/>(ADK Loop制御)"| RG
    RV ==>|"承認"| Orch

    %% データ接続
    CR --> FS
    CR --> MB
    CR --> SRC
    VA --> GVision
    RG --> GGen
    RV --> GRev

    %% 外部連携
    Voice --> Live
    Proactive --> Health
    SRC -.->|"URL登録時に構築"| Scraper

    %% フィードバックフライホイール（ループA）
    User -.->|"FB: 不採用タグ / 星評価 / 自由記述"| FS
    User -.->|"自由記述FB"| MB
```

---

## 図B：DevOps 自己改善パイプライン（ループB）

エージェント自身のプロンプト/ロジックを Git で管理し、**GitHub Actions の定期 eval（LLM-as-judge 回帰テスト）**で
提案品質を監視する。品質スコアが低下すると**改善 PR を自動起票（＋Slack 通知）**、
**人間のレビュー（Human-in-the-loop）**を経て main へマージすると Cloud Run へ自動デプロイされ、
**Cloud Trace** による可観測性とアウトカム・ダッシュボードへループが還る。

```mermaid
flowchart LR
    subgraph Repo["📦 GitHub リポジトリ（層2: 嗜好抽出ロジック/プロンプトを Git 管理）"]
        Prompts["prompts/*.md<br/>エージェントのプロンプト・ルール"]
        Main[("main ブランチ")]
    end

    Eval["⏱️ GitHub Actions 定期 eval<br/>(eval.yml / cron 毎日)<br/>LLM-as-judge 回帰テスト"]
    Judge{"提案品質スコア<br/>低下？"}

    Auto["🤖 Auto Improve<br/>(auto_improve.yml)<br/>修正案を含む改善PRを自動起票"]
    Slack["💬 Slack 通知"]
    Human["👤 人間レビュー<br/>(Human-in-the-loop)<br/>PR diff レビュー・承認"]

    CI["✅ CI (ci.yml)<br/>ユニットテスト / Docker build"]
    Deploy["🚀 Deploy (deploy.yml)<br/>Artifact Registry → Cloud Run<br/>自動デプロイ + ヘルスチェック"]
    Run["☁️ Cloud Run<br/>本番稼働（ループA を提供）"]
    Trace["🔎 Cloud Trace<br/>可観測性 / span 計装"]
    Dash["📊 アウトカム・ダッシュボード<br/>食品ロス削減率 / 栄養達成率 / 献立決定時間"]

    Prompts --> Eval
    Eval --> Judge
    Judge -->|"低下あり"| Auto
    Judge -->|"問題なし"| Dash
    Auto --> Slack
    Auto --> Human
    Human -->|"承認 & マージ"| Main
    Main --> CI
    CI --> Deploy
    Deploy --> Run
    Run --> Trace
    Trace --> Dash
    Dash -.->|"実測FBが次のevalの燃料に"| Eval
    Run -.->|"エージェント改善が本番に反映"| Prompts
```

---

## 2つのループの関係（俯瞰）

- **ループA（燃料 / 製品のML学習）**: 図A。ユーザーFBで層2/層3を動的更新し、次回提案精度を上げる実行時機能。
- **ループB（駆動輪 / DevOps）**: 図B。蓄積FBをもとに開発者がエージェント自体を継続改善するライフサイクル。
- ループAだけでは DevOps ではない。**ループBがあって初めて「DevOps × AI Agent」**が成立する（SPEC §4）。
