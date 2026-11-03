terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # State is local by default. For shared use, switch to a GCS bucket:
  # backend "gcs" {
  #   bucket = "<state-bucket>"
  #   prefix = "agent-b"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
