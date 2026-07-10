# terraform.tfvars.example — 実際の値をコピーして terraform.tfvars に記入する（git 管理外）
# cp terraform.tfvars.example terraform.tfvars

project_id  = "agentic-ai-495701" # 例: agentic-ai-495701
github_repo = "TomorrowsMealPlanningBoard/webapp"

# region・name_prefix はデフォルト値（asia-northeast1 / tomorrows-meal）のままで通常は不要
# region      = "asia-northeast1"
# name_prefix = "tomorrows-meal"

# Cloud Run 環境変数
use_firestore   = "true"
use_memory_bank = "true"
# Issue #82 修正: gemini-embedding-001（多言語/日本語対応）を明示指定した Agent Engine に差し替え。
# 旧 Engine(6163772575114592256) はデフォルト text-embedding-005（英語専用）で日本語検索が破綻していた。
# 生成モデルは us-central1 で利用可能な gemini-2.5-flash を指定（gemini-3.x は同リージョン未提供）。
memory_bank_agent_engine_id = "1223394152633335808"
# Memory Bank(Agent Engine)は us-central1 に存在する。Cloud Run は asia-northeast1 だが
# 層3検索クライアントは必ずエンジンのリージョンを指す。
memory_bank_location = "us-central1"
# 層3検索タイムアウト（ウォーム後はサブ秒。コールドスタート等への保険）。
vector_search_timeout_sec = "8"
gemini_text_model         = "gemini-3.1-flash-lite"
gemini_text_location      = "global"
gemini_vision_model       = "gemini-3.1-flash-lite"
gemini_vision_location    = "global"
gemini_live_model         = "gemini-live-2.5-flash-native-audio"
gemini_live_location      = "us-central1"
google_client_id          = "502417872105-n1v4kn434n6g4muhoisk9ndlij9fes5q.apps.googleusercontent.com"

# Cloud Run リソース割り当て
# 審査期間（〜7/24頃）はコールドスタート（初回約12秒）を避けるため min=1 で常時1インスタンス起動。
# CPU は常時割り当て（cpu_idle=false, cloud_run.tf で固定）。層3(Memory Bank)の
# クロスリージョン非同期I/Oが await 中のCPUスロットリングで飢餓→タイムアウトしていたため。
# min=max=1 の常駐構成のため常時割り当てでも課金は1インスタンス固定。
# 審査終了後にコストを戻す場合は min を 0 に変更する。
cloud_run_min_instances = 1
cloud_run_max_instances = 1
cloud_run_memory        = "1Gi"
cloud_run_cpu           = "1"
