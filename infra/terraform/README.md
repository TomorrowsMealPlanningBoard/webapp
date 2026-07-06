# infra/terraform — AlloyDB(pgvector) プロビジョニング (Issue #28)

このディレクトリは AlloyDB for PostgreSQL クラスタ／インスタンス、pgvector拡張適用の前提となる
VPC/プライベートサービスアクセス、IAM認証接続に必要なIAM設定、Secret Manager による接続情報管理をコード化したものです。

**このディレクトリのコードは `terraform apply` を実行していません。** 実際のリソース作成（課金が発生します）は
ユーザーが手動で `terraform apply` を実行して行ってください。詳細な手順は
[`docs/alloydb_provisioning.md`](../../docs/alloydb_provisioning.md) を参照してください。

## 構成ファイル

| ファイル | 内容 |
| --- | --- |
| `versions.tf` | Terraform / providerバージョン制約、providerの設定 |
| `variables.tf` | 外部化された変数定義（プロジェクトIDはハードコードしない） |
| `network.tf` | VPCネットワーク、プライベートサービスアクセス（AlloyDB必須） |
| `alloydb.tf` | AlloyDBクラスタ・プライマリインスタンス、Secret Manager（接続情報） |
| `iam.tf` | Cloud Run実行SAへの `roles/alloydb.client` 付与、AlloyDB IAM認証ユーザー登録 |
| `outputs.tf` | 作成後に参照する出力値（インスタンス名、IP、シークレットID等） |
| `terraform.tfvars.example` | 変数の設定例（`terraform.tfvars` にコピーして使う） |

## クイックスタート（実行はユーザーが行うこと）

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars を編集し、cloud_run_service_account_email 等を実際の値に更新

export TF_VAR_alloydb_admin_password="$(openssl rand -base64 24)"

terraform init
terraform fmt -check
terraform validate
terraform plan   # ここで内容を必ず確認する
terraform apply  # 課金が発生するリソースが作成される
```

## 設計上の注意点

- **IAM認証を前提とし、アプリケーションはパスワードを直接扱わない。** `alloydb_admin_password` は
  pgvector拡張の有効化やマイグレーション適用など、初期プロビジョニング作業にのみ使用する想定。
- **常時起動しない運用（destroy前提）。** AlloyDBには「停止」機能がなく、起動している限り時間単位で
  課金され続ける。そのため本プロジェクトでは「必要な期間（例: ハッカソン審査週間）だけ `terraform apply`、
  使い終わったら `terraform destroy`」を繰り返す運用を前提にしている。
  - `google_alloydb_cluster.main` に `lifecycle.prevent_destroy` は**設定していない**（誤ってdestroyできない事故の方を避ける）。
  - `deletion_policy = "FORCE"` を設定し、インスタンス・バックアップが存在する状態でも `terraform destroy` が通るようにしている。
  - `continuous_backup_config.enabled = false`。都度destroyする運用ではバックアップストレージの継続課金が無駄になるため無効化。
  - **destroy するとデータは完全に失われる。** destroy前には必ず `pg_dump` 等でバックアップを取ること。
    手順は [`docs/alloydb_provisioning.md`](../../docs/alloydb_provisioning.md) を参照。
  - この運用上、**Cloud Runは「AlloyDBクラスタが存在する間だけ」DB機能を使える。** destroy後は
    `scripts/db_healthcheck.py` が接続エラーを返すのが正常な状態になる。
- **可用性・スペック:** `alloydb_availability_type` はデフォルト `ZONAL`（単一AZ・HAなし）、
  `alloydb_primary_machine_cpu_count` はデフォルト `2`（AlloyDBの最小構成）。
  都度作成/削除する運用かつハッカソン/MVP用途のため、コストを最小化する設定をデフォルトにしている。
- **VPC:** `create_vpc_network = true` の場合、専用VPCを新規作成する。既存VPCを使い回したい場合は
  `false` にして `vpc_network_name` に既存VPC名を指定する。
