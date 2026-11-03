output "cluster_name" {
  value = google_container_cluster.this.name
}

output "image_repository" {
  description = "Push the agent-b image here."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}/agent-b"
}

output "ingress_ip" {
  description = "Point the agent-b DNS A record at this address."
  value       = google_compute_global_address.ingress.address
}

output "redis_url" {
  description = "REDIS_URL for the agent-b-env secret."
  value       = "redis://:${google_redis_instance.oauth.auth_string}@${google_redis_instance.oauth.host}:${google_redis_instance.oauth.port}/0"
  sensitive   = true
}
