# Remote state in S3 with native locking (Terraform >= 1.10). Values come from
# `terraform init -backend-config=...` (deploy.sh passes them), so one module
# serves every client. Locally, for tests: `terraform init -backend=false`.
terraform {
  backend "s3" {}
}
