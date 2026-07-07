# infra/terraform-archive/alloydb-deprecated — AlloyDB(pgvector) IaC 歴史記録 (Issue #28→#79で撤去)

> ## ⚠️ 【撤去済み】このIaCは歴史的経緯としてのみ残しています
> **アーキテクチャ再検討（#61 クローズ時、Epic #75）により、TomorrowsMeal は AlloyDB を採用しません。**
> データ基盤は **Agent Platform Memory Bank**（好み学習）＋ 層1/層2/層3'の構造化DB（Firestore 等、ベクトルDB不使用）
> というフルマネージド構成に移行しました。詳細は [`../../../SPEC.md`](../../../SPEC.md) を参照。
>
> **Issue #79 で `infra/terraform/` から本ディレクトリへ移動し、全ファイルを `.tf.disabled` にリネームしました。**
> Terraformは `.tf` 拡張子のファイルしか読み込まないため、このディレクトリで `terraform init/apply` を実行しても
> リソースは一切作成されません（誤applyの物理的な防止）。実際に使う場合は `.disabled` を剥がし、
> 内容を精査した上で新しいディレクトリにコピーしてください（非推奨）。

このディレクトリは AlloyDB for PostgreSQL クラスタ／インスタンス、pgvector拡張適用の前提となる
VPC/プライベートサービスアクセス、IAM認証接続に必要なIAM設定、Secret Manager による接続情報管理をコード化したものです（当時は未apply、実リソースは存在しません）。

## 構成ファイル（すべて `.disabled` 拡張子）

| ファイル | 内容 |
| --- | --- |
| `versions.tf.disabled` | Terraform / providerバージョン制約、providerの設定 |
| `variables.tf.disabled` | 外部化された変数定義（プロジェクトIDはハードコードしない） |
| `network.tf.disabled` | VPCネットワーク、プライベートサービスアクセス（AlloyDB必須） |
| `alloydb.tf.disabled` | AlloyDBクラスタ・プライマリインスタンス、Secret Manager（接続情報） |
| `iam.tf.disabled` | Cloud Run実行SAへの `roles/alloydb.client` 付与、AlloyDB IAM認証ユーザー登録 |
| `outputs.tf.disabled` | 作成後に参照する出力値（インスタンス名、IP、シークレットID等） |
| `terraform.tfvars.example.disabled` | 変数の設定例 |

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
