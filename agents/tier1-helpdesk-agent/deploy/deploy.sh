#!/usr/bin/env bash
# =============================================================================
# Deploy the Tier 1 IT Help Desk Agent to AWS (ECS Fargate on Graviton).
#
#   deploy/deploy.sh <client>.env --plan     # validate + terraform plan, changes nothing
#   deploy/deploy.sh <client>.env            # deploy (asks before applying)
#   deploy/deploy.sh <client>.env --yes      # deploy without prompts (CI)
#   add --skip-smoke to skip the final HTTPS smoke test
#   deploy/deploy.sh <client>.env --destroy  # tear down AWS resources (asks you to type the client slug)
#
# Steps (idempotent, so re-running is safe; it stops at the first error):
#   1  preflight: tools, AWS identity, deploy.env validated by the app's own config rules
#   2  Terraform state bucket (S3, versioned, encrypted, private) + terraform init
#   3  database schema -> Supabase (if SUPABASE_DB_URL is set)
#   4  ECR repository, then build + push the linux/arm64 image (tag = git commit)
#   5  terraform apply (VPC, ALB + TLS, ECS service, secrets, alarms)
#   6  wait for the service to be stable
#   7  load SOPs / catalog / directory, import n8n workflows (if configured)
#   8  smoke test over HTTPS, then print the remaining manual steps
#
# Secrets never go on the command line or into the repo: they are written to a
# temporary 600-mode Terraform variables file that is deleted on exit. Terraform
# state holds them too, which is why the state bucket is private and encrypted.
# =============================================================================
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
TF_DIR="$ROOT/terraform"

ENV_FILE=""
PLAN_ONLY=0
ASSUME_YES=0
SKIP_SMOKE=0
DESTROY=0
for arg in "$@"; do
  case "$arg" in
    --plan) PLAN_ONLY=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --skip-smoke) SKIP_SMOKE=1 ;;
    --destroy) DESTROY=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) ENV_FILE="$arg" ;;
  esac
done
[[ -n "$ENV_FILE" ]] || { echo "usage: $0 <client>.env [--plan] [--yes]" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "no such file: $ENV_FILE" >&2; exit 2; }
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"

bold() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "    $1 [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "stopped by user"
}
trap 'die "step failed at line $LINENO (see output above)"' ERR

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TFVARS="$WORK/deploy.tfvars.json"

# ---------------------------------------------------------------------------
bold "1/8 Preflight"
# ---------------------------------------------------------------------------
for tool in aws terraform docker python3 git; do
  command -v "$tool" >/dev/null || die "'$tool' is not installed (see deploy/DEPLOY.md, prerequisites)"
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "python3 >= 3.11 required"
tf_version="$(terraform version -json | python3 -c 'import json,sys; print(json.load(sys.stdin)["terraform_version"])')"
python3 - "$tf_version" <<'PY' || die "Terraform >= 1.10 required (found $tf_version)"
import sys
major, minor = (int(x) for x in sys.argv[1].split(".")[:2])
sys.exit(0 if (major, minor) >= (1, 10) else 1)
PY
docker buildx version >/dev/null 2>&1 || die "docker buildx is required (Docker Desktop, or the buildx plugin)"
if ! docker buildx inspect --bootstrap 2>/dev/null | grep -q "linux/arm64"; then
  die "this Docker builder can't build linux/arm64 images. On Linux run once:
       docker run --privileged --rm tonistiigi/binfmt --install arm64"
fi

perm="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")"
[[ "$perm" == "600" || "$perm" == "400" ]] || note "warning: $ENV_FILE is mode $perm; run: chmod 600 $ENV_FILE"
if git -C "$ROOT" ls-files --error-unmatch "$ENV_FILE" >/dev/null 2>&1; then
  die "$ENV_FILE is tracked by git. It contains secrets: git rm --cached it and keep it local."
fi

# Isolated virtualenv for the deploy helpers (render_config, ingest, n8n import).
VENV="${DEPLOY_VENV:-$HERE/.venv}"
if [[ ! -x "$VENV/bin/python" ]]; then
  note "creating $VENV"
  python3 -m venv "$VENV"
fi
# Some virtualenvs (e.g. uv-created) ship without pip.
"$VENV/bin/python" -m pip --version >/dev/null 2>&1 || "$VENV/bin/python" -m ensurepip --upgrade >/dev/null
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$ROOT/requirements.txt"
PY="$VENV/bin/python"
cfg() { "$PY" "$HERE/render_config.py" "$ENV_FILE" --get "$1"; }

"$PY" "$HERE/render_config.py" "$ENV_FILE" --check || die "fix deploy.env and re-run"

REGION="$(cfg AWS_REGION)"
CLIENT="$(cfg CLIENT_SLUG)"
HOSTNAME_="$(cfg PUBLIC_HOSTNAME)"
TIER="$(cfg SERVICE_TIER)"; TIER="${TIER:-TIER_0_INCENTIVE}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" || die "AWS credentials not working (aws sts get-caller-identity)"
CALLER="$(aws sts get-caller-identity --query Arn --output text)"
note "AWS account $ACCOUNT as $CALLER, region $REGION"
note "client $CLIENT, tier $TIER, https://$HOSTNAME_"

# Immutable image tag: the commit, plus a timestamp if the tree has local changes.
TAG="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
if [[ -n "$(git -C "$ROOT" status --porcelain -- . ':!deploy/*.env' 2>/dev/null)" ]]; then
  TAG="$TAG-dirty-$(date -u +%Y%m%d%H%M%S)"
  note "uncommitted changes: image tag $TAG"
fi
OVERRIDE_TAG="$(cfg IMAGE_TAG)"; [[ -n "$OVERRIDE_TAG" ]] && TAG="$OVERRIDE_TAG"
"$PY" "$HERE/render_config.py" "$ENV_FILE" --image-tag "$TAG" --out "$TFVARS"

# ---------------------------------------------------------------------------
bold "2/8 Terraform state"
# ---------------------------------------------------------------------------
BUCKET="$(cfg TF_STATE_BUCKET)"; BUCKET="${BUCKET:-tier1-agent-tfstate-$ACCOUNT-$REGION}"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  note "state bucket s3://$BUCKET exists"
else
  [[ $DESTROY -eq 1 ]] && die "no state bucket s3://$BUCKET, so there's nothing deployed to destroy in this account/region"
  [[ $PLAN_ONLY -eq 1 ]] && die "state bucket s3://$BUCKET doesn't exist yet. Run without --plan once to create it (nothing else is applied without confirmation)."
  confirm "Create private, encrypted, versioned state bucket s3://$BUCKET?"
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
  note "created s3://$BUCKET"
fi
terraform -chdir="$TF_DIR" init -input=false -reconfigure \
  -backend-config="bucket=$BUCKET" -backend-config="key=clients/$CLIENT/terraform.tfstate" \
  -backend-config="region=$REGION" -backend-config="encrypt=true" -backend-config="use_lockfile=true" >/dev/null
note "state: s3://$BUCKET/clients/$CLIENT/terraform.tfstate"

if [[ $DESTROY -eq 1 ]]; then
  bold "Destroy"
  terraform -chdir="$TF_DIR" plan -destroy -input=false -var-file="$TFVARS" -out="$WORK/destroy.tfplan"
  note "This deletes every AWS resource listed above for $CLIENT (the state bucket and Supabase data are kept)."
  # A typed confirmation is required even with --yes. Automation can set DEPLOY_DESTROY_CONFIRM=<client>.
  answer="${DEPLOY_DESTROY_CONFIRM:-}"
  if [[ -z "$answer" ]]; then
    read -r -p "    Type the client slug ($CLIENT) to confirm: " answer
  fi
  [[ "$answer" == "$CLIENT" ]] || die "confirmation didn't match; nothing was destroyed"
  terraform -chdir="$TF_DIR" apply -input=false "$WORK/destroy.tfplan"
  bold "Destroyed $CLIENT"
  note "Secrets Manager keeps the secret 7 days (restore-secret, or delete with --force-delete-without-recovery)."
  exit 0
fi

if [[ $PLAN_ONLY -eq 1 ]]; then
  bold "Plan only (nothing will change)"
  terraform -chdir="$TF_DIR" plan -input=false -var-file="$TFVARS"
  note "Plan complete. Re-run without --plan to deploy."
  exit 0
fi

# ---------------------------------------------------------------------------
bold "3/8 Database schema"
# ---------------------------------------------------------------------------
DB_URL="$(cfg SUPABASE_DB_URL)"
if [[ -n "$DB_URL" ]]; then
  command -v psql >/dev/null || die "SUPABASE_DB_URL is set but 'psql' isn't installed (or clear it and use the SQL editor)"
  if ! PGCONNECT_TIMEOUT=15 psql "$DB_URL" -v ON_ERROR_STOP=1 -q \
      -f "$ROOT/supabase/migrations/001_init_schema.sql" > "$WORK/migration.log" 2>&1; then
    grep -v NOTICE "$WORK/migration.log" >&2 || true
    die "the schema migration failed (output above)"
  fi
  PGCONNECT_TIMEOUT=15 psql "$DB_URL" -tA -v ON_ERROR_STOP=1 -c \
    "select count(*) from pg_proc where proname in ('match_sop_chunks','try_reserve_llm_budget','register_outage_cluster')" \
    | grep -qx 3 || die "schema check failed after applying the migration"
  note "schema applied and verified (idempotent)"
else
  note "SUPABASE_DB_URL not set: make sure supabase/migrations/001_init_schema.sql has been run in the Supabase SQL editor"
  confirm "Has the schema been applied?"
fi

# ---------------------------------------------------------------------------
bold "4/8 Container image"
# ---------------------------------------------------------------------------
terraform -chdir="$TF_DIR" apply -input=false -auto-approve -var-file="$TFVARS" \
  -target=aws_ecr_repository.agent_repo -target=aws_ecr_lifecycle_policy.keep_recent >/dev/null
REPO_URL="$(terraform -chdir="$TF_DIR" output -raw ecr_repository_url)"
REPO_NAME="${REPO_URL#*/}"
if aws ecr describe-images --repository-name "$REPO_NAME" --image-ids "imageTag=$TAG" >/dev/null 2>&1; then
  note "image $REPO_URL:$TAG already pushed"
else
  aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO_URL%%/*}" >/dev/null
  note "building linux/arm64 image (first build takes a few minutes)"
  docker buildx build --platform linux/arm64 --provenance=false -t "$REPO_URL:$TAG" --push "$ROOT"
  note "pushed $REPO_URL:$TAG"
fi

# ---------------------------------------------------------------------------
bold "5/8 Infrastructure"
# ---------------------------------------------------------------------------
terraform -chdir="$TF_DIR" plan -input=false -var-file="$TFVARS" -out="$WORK/tfplan"
confirm "Apply this plan to AWS account $ACCOUNT?"
terraform -chdir="$TF_DIR" apply -input=false "$WORK/tfplan"

CLUSTER="$CLIENT-cluster"
SERVICE="$CLIENT-tier1-agent-service"
ALB_DNS="$(terraform -chdir="$TF_DIR" output -raw alb_dns_name)"

# ---------------------------------------------------------------------------
bold "6/8 Waiting for the service to become stable"
# ---------------------------------------------------------------------------
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE" \
  || die "service didn't stabilise. Check: aws logs tail /ecs/$CLIENT-tier1-agent --since 15m"
note "ECS service $SERVICE is stable"

# ---------------------------------------------------------------------------
bold "7/8 Knowledge and integrations"
# ---------------------------------------------------------------------------
# The loaders talk to Supabase directly with the service key from deploy.env.
export SUPABASE_URL SUPABASE_SERVICE_KEY VOYAGE_API_KEY
SUPABASE_URL="$(cfg SUPABASE_URL)"; SUPABASE_SERVICE_KEY="$(cfg SUPABASE_SERVICE_KEY)"; VOYAGE_API_KEY="$(cfg VOYAGE_API_KEY)"
SOP_DIR="$(cfg SOP_DIR)"
if [[ -n "$SOP_DIR" ]]; then
  ( cd "$ROOT" && "$PY" -m ingest.sync_docs --tenant "$CLIENT" --docs "$SOP_DIR" )
fi
CATALOG="$(cfg CATALOG_CSV)"; DIRECTORY="$(cfg DIRECTORY_CSV)"
loader_args=()
[[ -n "$CATALOG" ]] && loader_args+=(--catalog "$CATALOG")
[[ -n "$DIRECTORY" ]] && loader_args+=(--directory "$DIRECTORY")
if [[ ${#loader_args[@]} -gt 0 ]]; then
  ( cd "$ROOT" && "$PY" -m ingest.load_tenant_data --tenant "$CLIENT" ${loader_args[@]+"${loader_args[@]}"} )
fi
if [[ -n "$(cfg N8N_API_URL)" && -n "$(cfg N8N_API_KEY)" ]]; then
  N8N_API_URL="$(cfg N8N_API_URL)" N8N_API_KEY="$(cfg N8N_API_KEY)" "$PY" "$ROOT/integrations/n8n/deploy_workflows.py"
else
  [[ "$TIER" != "TIER_0_INCENTIVE" ]] && note "n8n API not configured: import integrations/n8n/workflows/*.json by hand (see integrations/n8n/README.md)"
fi

# ---------------------------------------------------------------------------
bold "8/8 Smoke test"
# ---------------------------------------------------------------------------
if [[ $SKIP_SMOKE -eq 1 ]]; then
  note "skipped (--skip-smoke). Run later: $PY $HERE/smoke_test.py --host $HOSTNAME_ --tenant $CLIENT --tier $TIER --secret ..."
else
connect_args=()
if ! python3 -c "import socket,sys; socket.gethostbyname(sys.argv[1])" "$HOSTNAME_" 2>/dev/null; then
  note "$HOSTNAME_ doesn't resolve yet, so testing through the load balancer directly"
  connect_args=(--connect "$ALB_DNS")
fi
"$PY" "$HERE/smoke_test.py" --host "$HOSTNAME_" --secret "$(cfg WEBHOOK_SECRET)" --tenant "$CLIENT" \
  --tier "$TIER" ${connect_args[@]+"${connect_args[@]}"} || die "smoke test failed. Logs: aws logs tail /ecs/$CLIENT-tier1-agent --since 15m"
fi

bold "Deployed"
next_steps=""
nl=$'\n'
if [[ -z "$(cfg ROUTE53_ZONE_ID)" ]]; then next_steps+="      - DNS: create CNAME $HOSTNAME_ -> $ALB_DNS$nl"; fi
next_steps+="      - Slack: in your Slack app, Event Subscriptions -> Retry, to verify https://$HOSTNAME_/webhooks/slack$nl"
if [[ -n "$(cfg ALARM_EMAIL)" ]]; then next_steps+="      - Confirm the SNS alarm subscription email sent to $(cfg ALARM_EMAIL)$nl"; fi
if [[ "$TIER" != "TIER_0_INCENTIVE" ]]; then
  next_steps+="      - n8n: set the variables in integrations/n8n/README.md and publish the 4 workflows$nl"
fi
cat <<EOF
    URL            https://$HOSTNAME_   (load balancer: $ALB_DNS)
    Tier           $TIER
    Image          $REPO_URL:$TAG
    Logs           aws logs tail /ecs/$CLIENT-tier1-agent --follow

    Remaining one-time steps (deploy/DEPLOY.md, section 5):
$next_steps
EOF
exit 0
