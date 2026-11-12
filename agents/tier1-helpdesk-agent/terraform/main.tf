# Zero-NAT ECS Fargate (Graviton/ARM64) deployment for one client tenant.
#
# Tasks run in public subnets with public IPs, so they reach Claude, Supabase,
# Voyage, Slack and Jira through the Internet Gateway: no NAT Gateway (~$33+/mo
# per AZ plus data processing). They accept traffic only from the ALB's
# security group. The ALB gives Slack/Teams/Jira a stable HTTPS URL, which
# they require, and which a bare task IP can't give (it changes on every deploy).

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { Project = "tier1-helpdesk-agent", Client = var.client_slug }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "client_slug" {
  type        = string
  description = "Short lowercase client identifier, e.g., acme-corp"
  validation {
    condition     = can(regex("^[a-z0-9-]{2,24}$", var.client_slug))
    error_message = "client_slug must be 2-24 chars of lowercase letters, digits and dashes."
  }
}

variable "acm_certificate_arn" {
  type        = string
  description = "ACM certificate for the webhook hostname (e.g. helpdesk.acme.com)"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "monthly_token_budget_usd" {
  type    = string
  default = "25.00"
}

# --- Commercial tier ----------------------------------------------------------

variable "service_tier" {
  type        = string
  default     = "TIER_0_INCENTIVE"
  description = "TIER_0_INCENTIVE (free trial), TIER_1_PRODUCTION, or TIER_2_MANAGED_OPS"
  validation {
    condition     = contains(["TIER_0_INCENTIVE", "TIER_1_PRODUCTION", "TIER_2_MANAGED_OPS"], var.service_tier)
    error_message = "service_tier must be TIER_0_INCENTIVE, TIER_1_PRODUCTION or TIER_2_MANAGED_OPS."
  }
}

variable "trial_expires_on" {
  type        = string
  default     = ""
  description = "Tier 0 only: YYYY-MM-DD after which tickets go to humans. Empty = no expiry."
  validation {
    condition     = var.trial_expires_on == "" || can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.trial_expires_on))
    error_message = "trial_expires_on must be YYYY-MM-DD or empty."
  }
}

variable "disabled_features" {
  type        = list(string)
  default     = []
  description = "Kill switch: features to turn off (can only remove, never add, features)."
}

variable "allowed_tenant_ids" {
  type        = list(string)
  default     = []
  description = "Extra tenants this deployment serves besides client_slug."
}

variable "public_hostname" {
  type        = string
  description = "DNS name on the ACM certificate, e.g. helpdesk.acme.com (n8n calls back here)."
}

# --- Model provider -----------------------------------------------------------

variable "llm_provider" {
  type    = string
  default = "anthropic"
  validation {
    condition     = contains(["anthropic", "bedrock"], var.llm_provider)
    error_message = "llm_provider must be anthropic or bedrock."
  }
}

variable "bedrock_model_arns" {
  type        = list(string)
  default     = ["*"]
  description = "Resources for bedrock-mantle:CreateInference. Narrow to the model ARNs you enable."
}

# --- Sizing -------------------------------------------------------------------

variable "task_cpu" {
  type    = string
  default = "512"
}

variable "task_memory" {
  type    = string
  default = "1024"
}

variable "max_count" {
  type    = number
  default = 4
}

variable "readonly_root_filesystem" {
  type        = bool
  default     = false
  description = "Opt in once verified on Fargate (uvicorn workers may need /dev/shm)."
}

variable "extra_environment" {
  type        = map(string)
  default     = {}
  description = <<-EOT
    Non-secret settings by env var name, e.g. N8N_APPROVAL_WEBHOOK_URL, N8N_NOTIFY_WEBHOOK_URL,
    SLACK_ESCALATION_CHANNEL, IDENTITY_PROVIDER, GRAPH_TENANT_ID, GRAPH_CLIENT_ID, OKTA_DOMAIN,
    IT_MANAGER_EMAIL, HELPDESK_NAME, JIRA_BASE_URL, JIRA_EMAIL. Secrets go in integration_secrets.
  EOT
  validation {
    condition     = alltrue([for k in keys(var.extra_environment) : !can(regex("(SECRET|TOKEN|PASSWORD|API_KEY|SERVICE_KEY)", k))])
    error_message = "extra_environment looks like it contains a secret; put it in integration_secrets instead."
  }
}

variable "allowed_ingress_cidrs" {
  type        = list(string)
  description = "Who may reach the ALB. Narrow to Slack/Jira/Cloudflare ranges if you can."
  default     = ["0.0.0.0/0"]
}

# Secrets. Values land in Terraform state, so use an encrypted remote backend
# (S3 + KMS), or set them in Secrets Manager out-of-band and leave these empty.
variable "supabase_url" {
  type      = string
  sensitive = true
}
variable "supabase_service_key" {
  type      = string
  sensitive = true
}
variable "llm_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Anthropic API key (required when llm_provider = anthropic)."
}
variable "voyage_api_key" {
  type      = string
  sensitive = true
}
variable "webhook_secret" {
  type      = string
  sensitive = true
}

variable "integration_secrets" {
  type        = map(string)
  sensitive   = true
  default     = {}
  description = <<-EOT
    Secret integration settings by env var name, e.g.
    SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, TEAMS_OUTGOING_WEBHOOK_SECRET, TEAMS_INCOMING_WEBHOOK_URL,
    JIRA_API_TOKEN, JIRA_WEBHOOK_SECRET, N8N_OUTBOUND_SIGNING_SECRET, N8N_CALLBACK_SECRET,
    GRAPH_CLIENT_SECRET, OKTA_API_TOKEN.
  EOT
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name      = "${var.client_slug}-tier1-agent"
  tier_rank = { TIER_0_INCENTIVE = 0, TIER_1_PRODUCTION = 1, TIER_2_MANAGED_OPS = 2 }[var.service_tier]
  anthropic = var.llm_provider == "anthropic"

  core_secrets = merge(
    {
      SUPABASE_URL         = var.supabase_url
      SUPABASE_SERVICE_KEY = var.supabase_service_key
      VOYAGE_API_KEY       = var.voyage_api_key
      WEBHOOK_SECRET       = var.webhook_secret
    },
    local.anthropic ? { LLM_API_KEY = var.llm_api_key } : {},
  )
  # Key names aren't secret; only the values are.
  core_secret_names        = concat(["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "VOYAGE_API_KEY", "WEBHOOK_SECRET"], local.anthropic ? ["LLM_API_KEY"] : [])
  integration_secret_names = nonsensitive(keys(var.integration_secrets))
  secret_keys              = concat(local.core_secret_names, local.integration_secret_names)

  base_environment = merge(
    {
      TENANT_ID                = var.client_slug
      SERVICE_TIER             = var.service_tier
      MONTHLY_TOKEN_BUDGET_USD = var.monthly_token_budget_usd
      LLM_PROVIDER             = var.llm_provider
      AWS_REGION               = var.aws_region
      PUBLIC_BASE_URL          = "https://${var.public_hostname}"
      ALLOWED_TENANT_IDS       = join(",", distinct(concat([var.client_slug], var.allowed_tenant_ids)))
      DISABLED_FEATURES        = join(",", var.disabled_features)
    },
    var.trial_expires_on != "" ? { TRIAL_EXPIRES_ON = var.trial_expires_on } : {},
  )
  environment = merge(var.extra_environment, local.base_environment) # base wins on conflicts

  # What each tier needs at startup (mirrors Settings.missing_core_settings), checked at plan time.
  outage_on      = !contains(var.disabled_features, "outage_detection")
  approvals_on   = local.tier_rank >= 1 && !contains(var.disabled_features, "manager_approvals")
  flywheel_on    = local.tier_rank >= 2 && !contains(var.disabled_features, "knowledge_flywheel")
  reports_on     = local.tier_rank >= 2 && !contains(var.disabled_features, "executive_reports")
  has_env        = { for k in ["N8N_APPROVAL_WEBHOOK_URL", "N8N_NOTIFY_WEBHOOK_URL", "SLACK_ESCALATION_CHANNEL"] : k => contains(keys(var.extra_environment), k) }
  has_secret     = { for k in ["N8N_OUTBOUND_SIGNING_SECRET", "N8N_CALLBACK_SECRET", "SLACK_BOT_TOKEN"] : k => contains(local.integration_secret_names, k) }
  tier2_channel  = (local.has_env["N8N_NOTIFY_WEBHOOK_URL"] && local.has_secret["N8N_OUTBOUND_SIGNING_SECRET"]) || (local.has_env["SLACK_ESCALATION_CHANNEL"] && local.has_secret["SLACK_BOT_TOKEN"])
  approvals_ok   = local.has_env["N8N_APPROVAL_WEBHOOK_URL"] && local.has_secret["N8N_OUTBOUND_SIGNING_SECRET"] && local.has_secret["N8N_CALLBACK_SECRET"]
  tier2_ready    = local.has_env["N8N_NOTIFY_WEBHOOK_URL"] && local.has_secret["N8N_OUTBOUND_SIGNING_SECRET"]
  flywheel_ready = local.has_secret["N8N_CALLBACK_SECRET"]
}

# ---------------------------------------------------------------------------
# 1. ECR repository for the agent image
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "agent_repo" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "keep_recent" {
  repository = aws_ecr_repository.agent_repo.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# 2. Lean VPC: two public subnets (the ALB needs two AZs), no NAT Gateway
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "${var.client_slug}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.client_slug}-public-${count.index}" }
}

resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "public_assoc" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public_rt.id
}

# Free gateway endpoint: ECR image layers come from S3 without internet egress.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public_rt.id]
}

# ---------------------------------------------------------------------------
# 3. Security groups: internet -> ALB (443) -> tasks (8000). Tasks egress HTTPS.
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb_sg" {
  name        = "${local.name}-alb-sg"
  description = "Public HTTPS for webhooks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS webhooks"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }
  ingress {
    description = "HTTP (redirected to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "fargate_sg" {
  name        = "${local.name}-task-sg"
  description = "Agent tasks: ALB-only ingress, HTTPS egress"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "From ALB only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_sg.id]
  }
  egress {
    description = "HTTPS to Claude, Supabase, Voyage, Slack, Jira, ECR, CloudWatch"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# 4. Load balancer (stable HTTPS endpoint for webhooks)
# ---------------------------------------------------------------------------

resource "aws_lb" "agent" {
  name                       = substr("${local.name}-alb", 0, 32)
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb_sg.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  idle_timeout               = 120
}

resource "aws_lb_target_group" "agent" {
  name                 = substr("${local.name}-tg", 0, 32)
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.agent.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.agent.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.agent.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# ---------------------------------------------------------------------------
# 5. Secrets Manager (keys never appear in the task definition)
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "agent" {
  name                    = "${local.name}/runtime"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "agent" {
  secret_id     = aws_secretsmanager_secret.agent.id
  secret_string = jsonencode(merge(local.core_secrets, var.integration_secrets))
}

# ---------------------------------------------------------------------------
# 6. CloudWatch logs (7-day retention to stop storage cost creep)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "agent_logs" {
  name              = "/ecs/${local.name}"
  retention_in_days = 7
}

# ---------------------------------------------------------------------------
# 7. IAM
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution_role" {
  name               = "${local.name}-exec-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_exec_policy" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "read_runtime_secret" {
  name = "read-runtime-secret"
  role = aws_iam_role.ecs_execution_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.agent.arn]
    }]
  })
}

# The task role grants only what the app calls: Claude via Bedrock, when selected.
resource "aws_iam_role" "ecs_task_role" {
  name               = "${local.name}-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "bedrock_inference" {
  count = local.anthropic ? 0 : 1
  name  = "bedrock-mantle-inference"
  role  = aws_iam_role.ecs_task_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock-mantle:CreateInference"]
      Resource = var.bedrock_model_arns
    }]
  })
}

# ---------------------------------------------------------------------------
# 8. ECS cluster, Graviton task definition, service
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "cluster" {
  name = "${var.client_slug}-cluster"
}

resource "aws_ecs_task_definition" "agent_task" {
  family                   = "${local.name}-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu    # 512 = 0.5 vCPU
  memory                   = var.task_memory # MiB
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64" # Graviton: ~20% cheaper than x86
  }

  container_definitions = jsonencode([{
    name                   = "tier1-agent"
    image                  = "${aws_ecr_repository.agent_repo.repository_url}:${var.image_tag}"
    essential              = true
    portMappings           = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    readonlyRootFilesystem = var.readonly_root_filesystem
    stopTimeout            = 30 # > uvicorn --timeout-graceful-shutdown 25
    healthCheck = {
      command     = ["CMD", "python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
    environment = [for k, v in local.environment : { name = k, value = v }]
    secrets = [
      for k in local.secret_keys : {
        name      = k
        valueFrom = "${aws_secretsmanager_secret.agent.arn}:${k}::"
      }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.agent_logs.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])

  # Fail at plan time rather than crash-looping on deploy (mirrors the app's startup check).
  lifecycle {
    precondition {
      condition     = !local.anthropic || nonsensitive(var.llm_api_key != "")
      error_message = "llm_provider = anthropic requires llm_api_key."
    }
    precondition {
      condition     = !local.outage_on || local.tier2_channel
      error_message = "Outage detection needs a Tier 2 channel: N8N_NOTIFY_WEBHOOK_URL (extra_environment) + N8N_OUTBOUND_SIGNING_SECRET, or SLACK_ESCALATION_CHANNEL + SLACK_BOT_TOKEN (integration_secrets)."
    }
    precondition {
      condition     = !local.approvals_on || local.approvals_ok
      error_message = "Tier 1 approvals need N8N_APPROVAL_WEBHOOK_URL (extra_environment) and N8N_OUTBOUND_SIGNING_SECRET + N8N_CALLBACK_SECRET (integration_secrets)."
    }
    precondition {
      condition     = !(local.flywheel_on || local.reports_on) || local.tier2_ready
      error_message = "Tier 2 needs N8N_NOTIFY_WEBHOOK_URL (extra_environment) and N8N_OUTBOUND_SIGNING_SECRET (integration_secrets)."
    }
    precondition {
      condition     = !local.flywheel_on || local.flywheel_ready
      error_message = "The Tier 2 knowledge flywheel needs N8N_CALLBACK_SECRET (integration_secrets) for SOP-draft approvals."
    }
  }
}

resource "aws_ecs_service" "agent_service" {
  name                              = "${local.name}-service"
  cluster                           = aws_ecs_cluster.cluster.id
  task_definition                   = aws_ecs_task_definition.agent_task.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 30

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.fargate_sg.id]
    assign_public_ip = true # egress via IGW instead of a NAT Gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.agent.arn
    container_name   = "tier1-agent"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.https, aws_secretsmanager_secret_version.agent]

  lifecycle {
    ignore_changes = [desired_count] # owned by autoscaling
  }
}

# ---------------------------------------------------------------------------
# 9. Autoscaling on CPU (tickets are I/O-bound; CPU tracks concurrency well enough)
# ---------------------------------------------------------------------------

resource "aws_appautoscaling_target" "agent" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.cluster.name}/${aws_ecs_service.agent_service.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.desired_count
  max_capacity       = var.max_count
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${local.name}-cpu"
  service_namespace  = aws_appautoscaling_target.agent.service_namespace
  resource_id        = aws_appautoscaling_target.agent.resource_id
  scalable_dimension = aws_appautoscaling_target.agent.scalable_dimension
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "ecr_repository_url" {
  value = aws_ecr_repository.agent_repo.repository_url
}

output "alb_dns_name" {
  description = "Point your webhook hostname's CNAME here"
  value       = aws_lb.agent.dns_name
}

output "public_base_url" {
  value = "https://${var.public_hostname}"
}

output "service_tier" {
  value = var.service_tier
}

output "webhook_paths" {
  description = "Routes available at this tier (others return 404)"
  value = concat(
    ["/v1/triage", "/v1/telemetry/{tenant_id}/roi-summary", "/webhooks/slack", "/webhooks/teams", "/webhooks/jira"],
    local.tier_rank >= 1 ? ["/v1/callbacks/approval"] : [],
    local.tier_rank >= 2 ? ["/v1/webhooks/ticket-resolved", "/v1/callbacks/sop-draft", "/v1/reports/weekly"] : [],
  )
}
