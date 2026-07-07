# infra/terraform

AlloyDB(pgvector) 採用時のIaC（Issue #28）は、アーキテクチャ再検討（Epic #75）に伴い撤去した。
歴史記録は [`../terraform-archive/alloydb-deprecated/`](../terraform-archive/alloydb-deprecated/) を参照（`.tf.disabled` にリネーム済みで `terraform apply` 不可）。

移行後のデータ基盤は以下の2種類（Cloud Run実行SAのIAM(ADC)のみで接続でき、専用のTerraform IaCを必要としない）:

- **Agent Platform Memory Bank**（層3の好み学習）— Agent Engineのプロビジョニングは #82 で対応予定
- **構造化DB（Firestore 等）**（層1/層2/層3'）— `USE_FIRESTORE=true` で有効化（Issue #76）

新たにTerraformでの管理が必要になった場合は、このディレクトリに追加すること。
