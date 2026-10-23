# Infrastructure as code for the Proof-Carrying Pipeline on Google Cloud.
# Covers: Artifact Registry, the production Cloud Run service, two service
# accounts (a deployer, and a READ-ONLY verifier: no identity can both deploy
# and verify), and Workload Identity Federation so GitLab CI authenticates by
# OIDC with no long-lived key. Ephemeral per-MR staging services are created/destroyed
# by the pipeline (infra/deploy_staging.sh), not by Terraform, so they stay cheap.
#
# One-command stand-up:  cd infra && terraform init && terraform apply

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "gitlab_project_path" {
  type        = string
  description = "e.g. your-group/pcp-lifeaftercode"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "pcp"
  format        = "DOCKER"
  depends_on    = [google_project_service.services]
}

resource "google_service_account" "deployer" {
  account_id   = "pcp-deployer"
  display_name = "PCP CI deployer"
}

resource "google_project_iam_member" "deployer_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# The verifier only reads: which revision serves traffic, and request metrics.
resource "google_service_account" "verifier" {
  account_id   = "pcp-verifier"
  display_name = "PCP verifier (read-only)"
}

resource "google_project_iam_member" "verifier_ro" {
  for_each = toset(["roles/run.viewer", "roles/monitoring.viewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.verifier.email}"
}

resource "google_project_iam_member" "deployer_ar" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# --- Workload Identity Federation: GitLab CI OIDC -> deployer SA ---
resource "google_iam_workload_identity_pool" "gitlab" {
  workload_identity_pool_id = "gitlab-pool"
  display_name              = "GitLab CI pool"
}

resource "google_iam_workload_identity_pool_provider" "gitlab" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.gitlab.workload_identity_pool_id
  workload_identity_pool_provider_id = "gitlab-oidc"
  display_name                       = "GitLab OIDC"
  attribute_mapping = {
    "google.subject"           = "assertion.sub"
    "attribute.project_path"   = "assertion.project_path"
    "attribute.namespace_path" = "assertion.namespace_path"
    "attribute.ref"            = "assertion.ref"
  }
  # gitlab.com is a shared issuer: only tokens minted for THIS project count.
  attribute_condition = "assertion.project_path == \"${var.gitlab_project_path}\""
  oidc {
    issuer_uri        = "https://gitlab.com"
    allowed_audiences = ["https://gitlab.com"]
  }
}

resource "google_service_account_iam_member" "wif_bind" {
  for_each           = { deployer = google_service_account.deployer.name, verifier = google_service_account.verifier.name }
  service_account_id = each.value
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.gitlab.name}/attribute.project_path/${var.gitlab_project_path}"
}

# Production service (initial placeholder image; the pipeline promotes digests).
resource "google_cloud_run_v2_service" "prod" {
  name     = "pcp-prod"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"
  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
    }
    max_instance_request_concurrency = 80
  }
  # The pipeline promotes verified digests; Terraform must not revert them.
  lifecycle {
    ignore_changes = [template, client, client_version, labels]
  }
  depends_on = [google_project_service.services]
}

resource "google_cloud_run_v2_service_iam_member" "prod_public" {
  name     = google_cloud_run_v2_service.prod.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "prod_url" { value = google_cloud_run_v2_service.prod.uri }
output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/pcp"
}
output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.gitlab.name
}
output "deployer_sa" { value = google_service_account.deployer.email }
output "verifier_sa" { value = google_service_account.verifier.email }

output "gitlab_ci_variables" {
  description = "Paste into GitLab > Settings > CI/CD > Variables"
  value = <<-EOT
    GCP_PROJECT=${var.project_id}
    GCP_REGION=${var.region}
    GCP_WIF_PROVIDER=${google_iam_workload_identity_pool_provider.gitlab.name}
    GCP_DEPLOY_SA=${google_service_account.deployer.email}
    GCP_VERIFIER_SA=${google_service_account.verifier.email}
    PROD_URL=${google_cloud_run_v2_service.prod.uri}
  EOT
}
