locals {
  services = [
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "redis.googleapis.com",
  ]
}

resource "google_project_service" "this" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# --- Network -----------------------------------------------------------------

resource "google_compute_network" "this" {
  name                    = var.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.this]
}

resource "google_compute_subnetwork" "this" {
  name          = var.name
  network       = google_compute_network.this.id
  region        = var.region
  ip_cidr_range = "10.10.0.0/20"

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# --- GKE (Autopilot) ---------------------------------------------------------
# Autopilot: no node pools to size or patch; billing is per pod request, which
# suits a single small replica.

resource "google_container_cluster" "this" {
  name     = var.name
  location = var.region

  enable_autopilot = true
  network          = google_compute_network.this.id
  subnetwork       = google_compute_subnetwork.this.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  release_channel {
    channel = "REGULAR"
  }

  depends_on = [google_project_service.this]
}

# --- Container registry ------------------------------------------------------

resource "google_artifact_registry_repository" "this" {
  repository_id = var.name
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.this]
}

# --- Public IP for the Ingress (OAUTH_PUBLIC_URL points here) ----------------

resource "google_compute_global_address" "ingress" {
  name       = "${var.name}-ip"
  depends_on = [google_project_service.this]
}

# --- Redis for OAuth state (REDIS_URL) ---------------------------------------
# Holds DCR clients and refresh tokens so Claude Desktop connections survive
# pod restarts. Smallest tier; losing it only forces users to reconnect.

resource "google_redis_instance" "oauth" {
  name               = "${var.name}-oauth"
  region             = var.region
  tier               = "BASIC"
  memory_size_gb     = 1
  redis_version      = "REDIS_7_2"
  authorized_network = google_compute_network.this.id
  auth_enabled       = true
  depends_on         = [google_project_service.this]
}
