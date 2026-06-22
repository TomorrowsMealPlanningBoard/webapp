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
