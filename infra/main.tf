# Infrastructure as code for Polygraph on Google Cloud.
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
  description = "e.g. SoujanyaChatti/polygraph"
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

# Three identities, so no CI job holds more than its step needs:
#   pcp-deployer  production deploys/rollback; ONLY protected-branch jobs
#   pcp-staging   per-MR staging services + image pushes; any branch
#   pcp-verifier  read-only: which revision serves traffic, request metrics
# Caveat: pcp-staging needs roles/run.admin (to allow unauthenticated staging),
# which is project-wide. For hard isolation, run production in its own project.
resource "google_service_account" "deployer" {
  account_id   = "pcp-deployer"
  display_name = "PCP production deployer (protected branches only)"
  depends_on   = [google_project_service.services]
}

resource "google_service_account" "staging" {
  account_id   = "pcp-staging"
  display_name = "PCP staging deployer"
  depends_on   = [google_project_service.services]
}

resource "google_service_account" "verifier" {
  account_id   = "pcp-verifier"
  display_name = "PCP verifier (read-only)"
  depends_on   = [google_project_service.services]
}

locals {
  roles = {
    deployer_run     = ["deployer", "roles/run.admin"]
    deployer_sa_user = ["deployer", "roles/iam.serviceAccountUser"]
    staging_run      = ["staging", "roles/run.admin"]
    staging_sa_user  = ["staging", "roles/iam.serviceAccountUser"]
    staging_ar       = ["staging", "roles/artifactregistry.writer"]
    verifier_run     = ["verifier", "roles/run.viewer"]
    verifier_mon     = ["verifier", "roles/monitoring.viewer"]
  }
  sa_email = {
    deployer = google_service_account.deployer.email
    staging  = google_service_account.staging.email
    verifier = google_service_account.verifier.email
  }
}

resource "google_project_iam_member" "roles" {
  for_each = local.roles
  project  = var.project_id
  role     = each.value[1]
  member   = "serviceAccount:${local.sa_email[each.value[0]]}"
}

# --- Workload Identity Federation: GitLab CI OIDC -> service accounts ---
resource "google_iam_workload_identity_pool" "gitlab" {
  workload_identity_pool_id = "gitlab-pool"
  display_name              = "GitLab CI pool"
  depends_on                = [google_project_service.services]
}

resource "google_iam_workload_identity_pool_provider" "gitlab" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.gitlab.workload_identity_pool_id
  workload_identity_pool_provider_id = "gitlab-oidc"
  display_name                       = "GitLab OIDC"
  attribute_mapping = {
    # google.subject is capped at 127 bytes; GitLab's `sub` can exceed it.
    "google.subject"           = "\"gitlab:\" + assertion.project_id + \":\" + assertion.job_id"
    "attribute.project_path"   = "assertion.project_path"
    "attribute.namespace_path" = "assertion.namespace_path"
    "attribute.ref"            = "assertion.ref"
    "attribute.ref_protected"  = "assertion.ref_protected"
  }
  # gitlab.com is a shared issuer: only tokens minted for THIS project count.
  attribute_condition = "assertion.project_path == \"${var.gitlab_project_path}\""
  oidc {
    issuer_uri        = "https://gitlab.com"
    allowed_audiences = ["https://gitlab.com"]
  }
}

locals {
  pool_principals = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.gitlab.name}"
}

# Production deployer: only jobs on protected refs (main) of this project.
resource "google_service_account_iam_member" "wif_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.pool_principals}/attribute.ref_protected/true"
}

# Staging + verifier: any job of this project (the provider condition already
# restricts tokens to this project).
resource "google_service_account_iam_member" "wif_any_ref" {
  for_each           = { staging = google_service_account.staging.name, verifier = google_service_account.verifier.name }
  service_account_id = each.value
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.pool_principals}/attribute.project_path/${var.gitlab_project_path}"
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
output "staging_sa" { value = google_service_account.staging.email }

output "gitlab_ci_variables" {
  description = "Paste into GitLab > Settings > CI/CD > Variables"
  value = <<-EOT
    GCP_PROJECT=${var.project_id}
    GCP_REGION=${var.region}
    GCP_WIF_PROVIDER=${google_iam_workload_identity_pool_provider.gitlab.name}
    GCP_DEPLOY_SA=${google_service_account.deployer.email}
    GCP_STAGING_SA=${google_service_account.staging.email}
    GCP_VERIFIER_SA=${google_service_account.verifier.email}
    PROD_URL=${google_cloud_run_v2_service.prod.uri}
  EOT
}
