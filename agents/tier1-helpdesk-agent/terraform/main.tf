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
  type      = string
  sensitive = true
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
    Optional integration settings by env var name, e.g.
    SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_ESCALATION_CHANNEL,
    TEAMS_OUTGOING_WEBHOOK_SECRET, TEAMS_INCOMING_WEBHOOK_URL,
    JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_WEBHOOK_SECRET.
  EOT
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "${var.client_slug}-tier1-agent"
  core_secrets = {
    SUPABASE_URL         = var.supabase_url
    SUPABASE_SERVICE_KEY = var.supabase_service_key
    LLM_API_KEY          = var.llm_api_key
    VOYAGE_API_KEY       = var.voyage_api_key
    WEBHOOK_SECRET       = var.webhook_secret
  }
  # Key names aren't secret; only the values are.
  secret_keys = concat(keys(local.core_secrets), nonsensitive(keys(var.integration_secrets)))
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

# The app calls no AWS APIs, so the task role has no permissions.
resource "aws_iam_role" "ecs_task_role" {
  name               = "${local.name}-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
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
  cpu                      = "512"  # 0.5 vCPU
  memory                   = "1024" # 1 GB RAM
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64" # Graviton: ~20% cheaper than x86
  }

  container_definitions = jsonencode([{
    name         = "tier1-agent"
    image        = "${aws_ecr_repository.agent_repo.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "TENANT_ID", value = var.client_slug },
      { name = "MONTHLY_TOKEN_BUDGET_USD", value = var.monthly_token_budget_usd },
    ]
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

output "webhook_paths" {
  value = ["/v1/triage", "/webhooks/slack", "/webhooks/teams", "/webhooks/jira"]
}
