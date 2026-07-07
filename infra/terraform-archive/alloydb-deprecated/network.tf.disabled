# AlloyDBはVPC内プライベートIPでのみ提供されるため、
# プライベートサービスアクセス（VPCピアリング）の設定が必須。
# 参考: https://cloud.google.com/alloydb/docs/configure-connectivity

resource "google_project_service" "servicenetworking" {
  project            = var.project_id
  service            = "servicenetworking.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "alloydb" {
  project            = var.project_id
  service            = "alloydb.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "compute" {
  project            = var.project_id
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "servicenetworking_connections" {
  project            = var.project_id
  service            = "service-networking.googleapis.com"
  disable_on_destroy = false
}

# --- VPC ネットワーク ---
# 既存VPCを使う場合は create_vpc_network=false にし、data sourceで参照する。
resource "google_compute_network" "vpc" {
  count                   = var.create_vpc_network ? 1 : 0
  project                 = var.project_id
  name                    = var.vpc_network_name
  auto_create_subnetworks = false

  depends_on = [google_project_service.compute]
}

data "google_compute_network" "existing_vpc" {
  count   = var.create_vpc_network ? 0 : 1
  project = var.project_id
  name    = var.vpc_network_name
}

locals {
  vpc_network_id        = var.create_vpc_network ? google_compute_network.vpc[0].id : data.google_compute_network.existing_vpc[0].id
  vpc_network_name      = var.create_vpc_network ? google_compute_network.vpc[0].name : data.google_compute_network.existing_vpc[0].name
  vpc_network_self_link = var.create_vpc_network ? google_compute_network.vpc[0].self_link : data.google_compute_network.existing_vpc[0].self_link
}

# --- プライベートサービスアクセス用のIP範囲確保 ---
resource "google_compute_global_address" "private_ip_alloc" {
  project       = var.project_id
  name          = "${var.name_prefix}-alloydb-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.private_services_access_range
  network       = local.vpc_network_id

  depends_on = [google_project_service.compute]
}

# --- VPCピアリング接続の確立（AlloyDBが内部で利用するサービスネットワーキング） ---
resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = local.vpc_network_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]

  depends_on = [
    google_project_service.servicenetworking,
    google_project_service.servicenetworking_connections,
  ]
}

# --- Cloud Run から AlloyDB (プライベートIP) へ到達するためのVPCコネクタ用サブネット ---
# Cloud Run自体のVPCコネクタ設定は既存のdeploy.yml/Cloud Run側の責務とするが、
# AlloyDBと同一VPCで疎通できるようサブネットを用意しておく。
resource "google_compute_subnetwork" "connector_subnet" {
  count         = var.create_vpc_network ? 1 : 0
  project       = var.project_id
  name          = "${var.name_prefix}-connector-subnet"
  ip_cidr_range = "10.8.0.0/28"
  region        = var.region
  network       = local.vpc_network_id

  depends_on = [google_project_service.compute]
}
