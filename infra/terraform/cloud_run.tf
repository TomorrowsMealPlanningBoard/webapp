# Cloud Run サービスの宣言的管理。
# 実行 SA・環境変数（非機密）・リソース割り当てを Terraform で管理し、
# deploy.yml はイメージ更新のみ行う構成にする。

resource "google_cloud_run_v2_service" "webapp" {
  name     = "${var.name_prefix}-webapp"
  location = var.region
  project  = var.project_id

  # apis.tf の run.googleapis.com 有効化を待ってから作成する
  depends_on = [google_project_service.run]

  deletion_protection = false

  template {
    service_account = google_service_account.cloud_run.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      # 初回 apply 時のプレースホルダー。deploy.yml がイメージを上書きする。
      # Artifact Registry にイメージが存在しない初回は Google 公式の hello イメージを使う。
      # ignore_changes = [image] により、CI/CD がイメージを更新しても Terraform は上書きしない。
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      resources {
        limits = {
          memory = var.cloud_run_memory
          cpu    = var.cloud_run_cpu
        }
        # CPU 常時割り当て（アイドル時もスロットリングしない）。
        # 層3(Memory Bank)検索は google-genai の非同期 httpx で us-central1 へ
        # クロスリージョン呼び出しを行い、その大半は await（ネットワーク I/O 待ち）。
        # cpu_idle=true だと await 中に CPU がスロットリングされ、ADC 解決
        # (メタデータサーバ往復)・TLS/接続確立・レスポンス処理が CPU 飢餓で
        # 極端に遅延し、層3が 10 秒でもタイムアウトしていた（本番実測で確定）。
        # min_instances=max_instances=1 の常駐構成では追加インスタンスは増えず、
        # 常時割り当てにしても課金インパクトは限定的（1 インスタンス固定）。
        cpu_idle = false
      }

      # 非機密の環境変数（機密値は Issue #92 で Secret Manager 経由に移行）
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "USE_FIRESTORE"
        value = var.use_firestore
      }
      env {
        name  = "USE_MEMORY_BANK"
        value = var.use_memory_bank
      }
      env {
        name  = "MEMORY_BANK_AGENT_ENGINE_ID"
        value = var.memory_bank_agent_engine_id
      }
      # Memory Bank(Agent Engine)のリージョン。エンジンは us-central1 に存在するため、
      # Cloud Run(asia-northeast1)からは必ずこのリージョンのエンドポイントを叩く。
      env {
        name  = "MEMORY_BANK_LOCATION"
        value = var.memory_bank_location
      }
      # 層3ベクトル検索(Memory Bank)のタイムアウト秒数（フォールバックの保険）。
      env {
        name  = "VECTOR_SEARCH_TIMEOUT_SEC"
        value = var.vector_search_timeout_sec
      }
      # ログレベル。層3完了ログ(INFO)を本番で観測できるようにする。
      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }
      env {
        name  = "GEMINI_TEXT_MODEL"
        value = var.gemini_text_model
      }
      env {
        name  = "GEMINI_TEXT_LOCATION"
        value = var.gemini_text_location
      }
      env {
        name  = "GEMINI_VISION_MODEL"
        value = var.gemini_vision_model
      }
      env {
        name  = "GEMINI_VISION_LOCATION"
        value = var.gemini_vision_location
      }
      env {
        name  = "GEMINI_LIVE_MODEL"
        value = var.gemini_live_model
      }
      env {
        name  = "GEMINI_LIVE_LOCATION"
        value = var.gemini_live_location
      }
      env {
        name  = "GOOGLE_CLIENT_ID"
        value = var.google_client_id
      }
      # JWT署名鍵は Secret Manager から参照する（Issue #92）
      env {
        name = "JWT_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    # deploy.yml が image タグを書き換えるため、image の差分を無視する
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }
}

# allUsers に roles/run.invoker を付与して --allow-unauthenticated 相当にする
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.webapp.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
