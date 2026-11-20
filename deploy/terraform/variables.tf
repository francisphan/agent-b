variable "project_id" {
  description = "GCP project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for the cluster, registry and Redis."
  type        = string
  default     = "us-east1"
}

variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "agent-b"
}
