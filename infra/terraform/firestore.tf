# Firestore Native モードのデータベース（層1/層2/層3'の構造化DB）
# ロケーションはリージョンに合わせる。一度作成したらロケーション変更不可。
resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # Firestore は削除すると全データが失われる。Terraform destroy での誤削除を防ぐ。
  lifecycle {
    prevent_destroy = true
  }
}
