# アーキテクチャ図メモ（Proto Pedia 用）

> [architecture.md](architecture.md) の Mermaid 図を PNG 化して Proto Pedia の「システム構成図」（必須アップロード項目）に載せるための補足資料。
> 図の意図・使用 GCP プロダクト一覧・技術的補足・PNG 化手順をまとめる。

---

## 1. Proto Pedia「システム構成」欄に貼る説明文（コピペ用）

TomorrowsMeal は「毎日3食の献立を提案するAIエージェント」を、2つのループで構成しています。

**ループA（ランタイム）**: ユーザーはスマホから「冷蔵庫の写真・気分・調理時間」を送信します。Cloud Run（FastAPI）内の Google ADK Orchestrator が、役割特化した4つのエージェント（Context Retriever／Vision Analyzer／Recipe Generator／Recipe Reviewer）を協調させ、「生成→監査→差し戻し」ループで安全な献立3案を返します。アレルギー・禁止食材といったハード制約（層1）は決定的フィルタ（if文）で機械的に除外し、好み学習（層3）は Agent Platform Memory Bank、外部レシピソース（層3'）は構造化DBで扱うことで、データ特性ごとに処理方式を分離しています。LLM推論はすべて Vertex AI（Gemini Enterprise Agent Platform）経由の Gemini で行い、調理中の音声相談は Gemini Live API、栄養連携は Google Health API を利用します。

**ループB（DevOps）**: エージェントのプロンプト／ロジックを Git で管理し、GitHub Actions の定期eval（LLM-as-judge回帰テスト）で提案品質を監視します。品質低下時には改善PRを自動起票（＋Slack通知）し、人間のレビュー（Human-in-the-loop）を経てmainにマージするとCloud Runへ自動デプロイ、Cloud Traceで可観測性を確保しダッシュボードにアウトカムを可視化します。ループAが「燃料」、ループBが「駆動輪」であり、両者が揃って初めて「DevOps × AI Agent」が成立します。

---

## 2. 使用 Google Cloud プロダクト一覧

| プロダクト | 用途 | 図 |
| --- | --- | --- |
| **Cloud Run** | バックエンド（FastAPI）+ ADK 4エージェント + 静的フロントエンド（`app/static/`）を1コンテナに集約してホスト | A / B |
| **Vertex AI（Gemini Enterprise Agent Platform）** | Gemini 呼び出しの基盤（Vision / 生成 / 監査） | A |
| **Gemini（Vision / Flash / Pro 相当）** | 冷蔵庫画像の構造化認識、レシピ生成、制約監査 | A |
| **Gemini Live API** | 調理中のリアルタイム音声インタラクション（Tier2） | A |
| **Agent Platform Memory Bank** | 層3：自由記述FBからの好み学習（フルマネージド長期記憶・類似度検索） | A |
| **Firestore** | 層1（ハード制約・決定的フィルタ）/ 層2（構造化FB）/ 層3'（外部レシピソース） | A |
| **Google Health API（Fitness）** | 直近の栄養バランスに基づく能動提案（拡張） | A |
| **GitHub Actions** | 定期eval / 自動改善PR起票 / CI / Cloud Run 自動デプロイ | B |
| **Artifact Registry** | Docker イメージのビルド成果物の保管 | B |
| **Cloud Trace** | OpenTelemetry によるエージェント処理の可観測性（span 自動計装） | B |
| **Workload Identity Federation** | GitHub Actions → GCP のキーレス認証（deploy.yml） | B |
| **Terraform** | インフラ構成管理（terraform-plan / terraform-apply ワークフロー） | B |

> 補助的な外部連携: Slack（改善PR通知）、Web Scraper（YouTube/ブログ取得 → 層3'）。

---

## 3. 図の意図（審査基準への訴求ポイント）

- **AIエージェントが価値の中心であること**: 図Aの中央に ADK Orchestrator と4エージェントを配置し、「生成→監査→差し戻しループ」を実線・破線で明示。単一巨大プロンプトではなく役割特化のマルチエージェント協調であることを一目で示す。
- **実装力**: 図Bで「eval → 自動PR → レビュー → デプロイ → Trace → ダッシュボード」の完全な自己改善パイプラインを描き、実在する GitHub Actions（eval.yml / auto_improve.yml / ci.yml / deploy.yml / terraform-*.yml）に対応させている。
- **設計思想（安全と確率の分離）**: 層1（決定的フィルタ）と層3（確率的なMemory Bank）を別ノード・別データストアとして描き分け、「アレルギー事故を確率的検索に委ねない」というガードレール設計を可視化。

---

## 4. 技術的補足（なぜこの設計か）

### なぜ4エージェントをマイクロサービス化せず1つの Cloud Run に集約したか（SPEC §6.3）
1. **ネットワーク遅延の回避**: 「生成→監査→差し戻し（Loop）」のたびにCloud Run間HTTPS通信が発生すると、LLM推論時間に通信遅延が重なり実用速度に達しない。1プロセス内なら引数の受け渡しで完結。
2. **状態管理の単純化**: 「どのユーザーのどの冷蔵庫画像か」というコンテキストをネットワーク越しに引き回す必要がなく、開発難易度が下がる。
3. **コスト**: 4コンテナが個別にコールドスタート・課金される無駄を避ける。
- ADK仕様上、4エージェントは1つのPythonプログラム内の「モジュール」として記述できるため、集約が自然。

### なぜ層1（アレルギー・禁止食材）は決定的フィルタか（SPEC §3 / CLAUDE.md §0.4）
- ベクトル検索は確率的挙動を持ち、アレルギー食材を稀に見落とす事故リスクがある。安全・ハード制約は `if` 文による機械的除外のみで実装し、確率に委ねない。
- 「確率的な記憶（Memory Bank）」と「決定的な安全ガードレール（Firestore + if文）」の意図的な分離そのものが設計の主張。

### フロントエンドの配信方式（実装: Cloud Run から静的配信）
- フロントエンドは軽量な静的ファイル（`app/static/index.html` / `app.js` / `style.css`）で、FastAPI の `StaticFiles` マウント＋ `FileResponse` によりバックエンドと同じ Cloud Run サービスから配信している（`app/main.py`）。
- これにより配信元を1サービスに集約でき、CORS やホスティング分離の運用を排除。将来的にビルドの重い SPA へ拡張する場合は、静的部分を Firebase Hosting 等の CDN へ切り出す余地がある（現状は不要と判断）。

### なぜ層3' はベクトルDBを使わないか（SPEC §3 / §5.4）
- お気に入りURLは1ユーザーあたり数件〜数十件の小規模。ベクトル検索は過剰設計であり、RAG Engine(RagManagedDb)は常時起動で約$65/月かかる。LLMで構造化した要約を全件プロンプト直接注入する方が安価かつ十分。

---

## 5. PNG 化手順

Mermaid CLI（`mmdc`）で PNG 化する。ローカルにグローバルインストールが無い場合は `npx` で実行できる。

### 図をまとめて出力（推奨・図Aと図Bを個別ファイルに）
架構図は2枚あるため、Proto Pedia には図A・図Bを別々の画像として、または縦結合した1枚としてアップロードする。
`mmdc` は Markdown 内の複数の ```mermaid ブロックを `-o out.png` 指定時に `out-1.png`, `out-2.png` … と連番出力する。

```bash
cd /home/tutti/repos/gcloud-devops-aiagent-hackathon2/webapp
# 白背景・高解像度で docs/architecture.md 内の全 mermaid ブロックを PNG 化
npx -y @mermaid-js/mermaid-cli \
  -i docs/architecture.md \
  -o docs/architecture.png \
  -b white \
  --scale 3
# → docs/architecture-1.png（図A）, docs/architecture-2.png（図B）が生成される
```

### 個別の .mmd から出力したい場合
architecture.md の ```mermaid ブロックの中身をそれぞれ `figA.mmd` / `figB.mmd` に貼り付けて:

```bash
npx -y @mermaid-js/mermaid-cli -i figA.mmd -o docs/architecture-loopA.png -b white --scale 3
npx -y @mermaid-js/mermaid-cli -i figB.mmd -o docs/architecture-loopB.png -b white --scale 3
```

### オンラインで作る場合（CLIが使えない環境）
1. https://mermaid.live/ を開く
2. architecture.md の各 ```mermaid ブロックの中身を貼り付ける
3. Actions → PNG（またはSVG）でダウンロード

### 注意点
- ノードラベル内の `/` や `<br/>` はそのまま使用可。丸括弧 `()` を含むラベルは二重引用符 `"..."` で囲んでいる（Mermaid構文エラー回避）。
- `flowchart TB`（図A・縦）/ `flowchart LR`（図B・横）でレイアウトを最適化済み。
