# terraform.tfvars.example — 実際の値をコピーして terraform.tfvars に記入する（git 管理外）
# cp terraform.tfvars.example terraform.tfvars

project_id  = "agentic-ai-495701" # 例: agentic-ai-495701
github_repo = "TomorrowsMealPlanningBoard/webapp"

# region・name_prefix はデフォルト値（asia-northeast1 / tomorrows-meal）のままで通常は不要
# region      = "asia-northeast1"
# name_prefix = "tomorrows-meal"

# Cloud Run 環境変数
use_firestore               = "true"
use_memory_bank             = "true"
memory_bank_agent_engine_id = "6163772575114592256"
gemini_text_model           = "gemini-3.1-flash-lite"
gemini_text_location        = "global"
gemini_vision_model         = "gemini-3.1-flash-lite"
gemini_vision_location      = "global"
gemini_live_model           = "gemini-live-2.5-flash-native-audio"
gemini_live_location        = "us-central1"
google_client_id            = "502417872105-n1v4kn434n6g4muhoisk9ndlij9fes5q.apps.googleusercontent.com"

# Cloud Run リソース割り当て
# 審査期間（〜7/24頃）はコールドスタート（初回約12秒）を避けるため min=1 で常時1インスタンス起動。
# CPU はリクエスト処理中のみ割り当て（cpu_idle=true）＝アイドル時はCPU課金を抑える。
# 審査終了後にコストを戻す場合は min を 0 に変更する。
cloud_run_min_instances = 1
cloud_run_max_instances = 1
cloud_run_memory        = "1Gi"
cloud_run_cpu           = "1"
