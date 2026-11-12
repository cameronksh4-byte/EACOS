# Plan-only tests with a mocked AWS provider (no credentials, nothing created):
#   cd terraform && terraform init -backend=false && terraform test

mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-1a", "us-east-1b"] }
  }
  override_data {
    target = data.aws_iam_policy_document.ecs_assume
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  client_slug          = "acme-corp"
  acm_certificate_arn  = "arn:aws:acm:us-east-1:123456789012:certificate/test"
  public_hostname      = "helpdesk.acme.com"
  supabase_url         = "https://x.supabase.co"
  supabase_service_key = "service"
  voyage_api_key       = "voyage"
  webhook_secret       = "secret"
  llm_api_key          = "sk-ant-test"
  extra_environment    = { SLACK_ESCALATION_CHANNEL = "C-ESC" }
  integration_secrets  = { SLACK_BOT_TOKEN = "xoxb-test" }
}

run "tier0_defaults" {
  command = plan

  assert {
    condition     = local.environment["SERVICE_TIER"] == "TIER_0_INCENTIVE"
    error_message = "default tier should be the free incentive tier"
  }
  assert {
    condition     = local.environment["PUBLIC_BASE_URL"] == "https://helpdesk.acme.com"
    error_message = "PUBLIC_BASE_URL not derived from public_hostname"
  }
  assert {
    condition     = contains(local.secret_keys, "LLM_API_KEY") && contains(local.secret_keys, "SLACK_BOT_TOKEN")
    error_message = "secrets not wired from Secrets Manager"
  }
  assert {
    condition     = length(aws_iam_role_policy.bedrock_inference) == 0
    error_message = "no Bedrock permissions on the Anthropic provider"
  }
  assert {
    condition     = !contains(output.webhook_paths, "/v1/callbacks/approval")
    error_message = "Tier 1 route advertised at Tier 0"
  }
}

run "tier0_without_tier2_channel_fails" {
  command = plan
  variables {
    extra_environment   = {}
    integration_secrets = {}
  }
  expect_failures = [aws_ecs_task_definition.agent_task]
}

run "tier0_outage_disabled_needs_no_channel" {
  command = plan
  variables {
    extra_environment   = {}
    integration_secrets = {}
    disabled_features   = ["outage_detection"]
  }
  assert {
    condition     = local.environment["DISABLED_FEATURES"] == "outage_detection"
    error_message = "kill switch not passed to the container"
  }
}

run "tier1_without_n8n_fails" {
  command = plan
  variables {
    service_tier = "TIER_1_PRODUCTION"
  }
  expect_failures = [aws_ecs_task_definition.agent_task]
}

run "tier1_with_n8n" {
  command = plan
  variables {
    service_tier = "TIER_1_PRODUCTION"
    extra_environment = {
      SLACK_ESCALATION_CHANNEL = "C-ESC"
      N8N_APPROVAL_WEBHOOK_URL = "https://n8n.acme.com/webhook/approvals"
      IDENTITY_PROVIDER        = "microsoft_graph"
    }
    integration_secrets = {
      SLACK_BOT_TOKEN             = "xoxb"
      N8N_OUTBOUND_SIGNING_SECRET = "out"
      N8N_CALLBACK_SECRET         = "in"
      GRAPH_CLIENT_SECRET         = "graph"
    }
  }
  assert {
    condition     = contains(output.webhook_paths, "/v1/callbacks/approval") && !contains(output.webhook_paths, "/v1/reports/weekly")
    error_message = "Tier 1 routes wrong"
  }
  assert {
    condition     = local.environment["IDENTITY_PROVIDER"] == "microsoft_graph"
    error_message = "extra_environment not passed through"
  }
}

run "tier2_needs_notify_and_callback" {
  command = plan
  variables {
    service_tier = "TIER_2_MANAGED_OPS"
    extra_environment = {
      SLACK_ESCALATION_CHANNEL = "C-ESC"
      N8N_APPROVAL_WEBHOOK_URL = "https://n8n.acme.com/webhook/approvals"
    }
    integration_secrets = {
      SLACK_BOT_TOKEN             = "xoxb"
      N8N_OUTBOUND_SIGNING_SECRET = "out"
      N8N_CALLBACK_SECRET         = "in"
    }
  }
  expect_failures = [aws_ecs_task_definition.agent_task]
}

run "tier2_complete" {
  command = plan
  variables {
    service_tier = "TIER_2_MANAGED_OPS"
    extra_environment = {
      N8N_APPROVAL_WEBHOOK_URL = "https://n8n.acme.com/webhook/approvals"
      N8N_NOTIFY_WEBHOOK_URL   = "https://n8n.acme.com/webhook/notify"
    }
    integration_secrets = {
      N8N_OUTBOUND_SIGNING_SECRET = "out"
      N8N_CALLBACK_SECRET         = "in"
    }
  }
  assert {
    condition     = contains(output.webhook_paths, "/v1/webhooks/ticket-resolved")
    error_message = "Tier 2 routes missing"
  }
}

run "bedrock_needs_no_api_key_and_gets_iam" {
  command = plan
  variables {
    llm_provider = "bedrock"
    llm_api_key  = ""
  }
  assert {
    condition     = length(aws_iam_role_policy.bedrock_inference) == 1
    error_message = "Bedrock permission missing"
  }
  assert {
    condition     = !contains(local.secret_keys, "LLM_API_KEY")
    error_message = "no Anthropic key should be injected on Bedrock"
  }
}

run "anthropic_without_key_fails" {
  command = plan
  variables {
    llm_api_key = ""
  }
  expect_failures = [aws_ecs_task_definition.agent_task]
}

run "secrets_rejected_from_plain_environment" {
  command = plan
  variables {
    extra_environment = { SLACK_ESCALATION_CHANNEL = "C-ESC", OKTA_API_TOKEN = "oops" }
  }
  expect_failures = [var.extra_environment]
}

run "invalid_tier_rejected" {
  command = plan
  variables {
    service_tier = "TIER_9_GOLD"
  }
  expect_failures = [var.service_tier]
}
