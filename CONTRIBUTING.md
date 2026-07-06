# 開発ガイドライン (CONTRIBUTING)

このプロジェクト（TomorrowsMeal webapp）への貢献方法、および開発環境の構築と起動方法について説明します。

## 開発環境のセットアップと起動方法

アプリケーションの起動には、**Docker を使用する方法（推奨）**と、**ローカルの Python 環境で直接実行する方法**の2通りがあります。

### 方法1: Docker Compose を使用する場合（推奨）

Docker 環境がインストールされている場合、以下のコマンドで簡単に起動できます。

1. **コンテナのビルドと起動**
   ```bash
   docker compose up --build
   ```
   * ※バックグラウンドで起動したい場合は、`docker compose up -d --build` としてください。

2. **アプリケーションへのアクセス**
   ブラウザで以下のURLを開きます。
   * **メイン画面:** [http://localhost:8000/](http://localhost:8000/) (Hello World が表示されます)
   * **ヘルスチェック API:** [http://localhost:8000/health](http://localhost:8000/health)

3. **コンテナの停止**
   ```bash
   docker compose down
   ```

> [!NOTE]
> ホストのコードがコンテナにマウントされているため、`app/` 内のコードを変更すると自動的にリロード（ホットリロード）されます。

---

### 方法2: ローカルの Python 環境で直接実行する場合

Python 3.11 以上がインストールされている環境で実行します。

1. **仮想環境の作成とアクティベート**
   ```bash
   python -m venv venv
   source venv/bin/activate  # macOS / Linux の場合
   # venv\Scripts\activate   # Windows の場合
   ```

2. **依存関係のインストール**
   ```bash
   pip install -r requirements.txt
   ```

3. **アプリケーションの起動**
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

4. **アプリケーションへのアクセス**
   ブラウザで以下のURLを開きます。
   * [http://localhost:8000/](http://localhost:8000/)

---

## 疎通確認 (curl)

```bash
# ヘルスチェック
curl -s http://localhost:8000/health

# プロファイルの取得
curl -s http://localhost:8000/api/profile
```

---

## テスト戦略

### テスト種別と実行タイミング

| 種別 | ディレクトリ | ツール | 実行タイミング |
|---|---|---|---|
| 単体テスト | `tests/unit/` | pytest + TestClient | push毎（CI自動）、Stop Hookでも自動実行 |
| E2Eテスト | `tests/e2e/` | Playwright | PR作成前にClaudeがローカルで手動実行 |
| インテグレーション | `tests/integration/` | pytest（モックなし） | ローカル手動のみ |

### テスト実行コマンド

```bash
# 単体テストのみ（CI相当）
uv run pytest tests/unit/ -v

# E2Eテスト（アプリ起動後に実行）
docker compose up -d
uv run pytest tests/e2e/ -v

# E2E + unit（PR作成前のローカル確認用）
uv run pytest tests/unit/ tests/e2e/ -v
```

### Lintコマンド

```bash
# チェックのみ
uv run ruff check .

# 自動修正込み
uv run ruff check --fix .
uv run ruff format .
```

### 実装完了の定義（アジャイル：常に動く状態を保つ）

以下がすべて満たされて初めて「完了」とする：

1. **ACの全項目がチェック済み**であること
2. **`uv run pytest tests/unit/` が全件パス**すること
3. **E2Eで該当機能が画面から操作できる**こと（Claudeが自律検証）
4. **既存テストがリグレッションしていない**こと
5. **PRが作成されチケットにリンク**されていること

### Claude Code による自律E2E検証の手順

実装完了後、Claude Code は以下を自律的に行う：

1. `docker compose up -d` でアプリを起動
2. `uv run pytest tests/e2e/ -v` を実行
3. 失敗した場合は修正して再試行
4. 全件パスを確認してからPRを作成

### チケットACの標準テンプレート

新チケットのACは以下の形式で書く：

```
## Acceptance Criteria
- [ ] （ユーザー視点）何が操作・確認できるか
- [ ] どのAPIが呼ばれ何が返るか（バックエンドの場合）
- [ ] `uv run pytest tests/unit/` が全件パスすること
- [ ] E2Eで画面から操作できること
- [ ] エラーケースの扱い
```
