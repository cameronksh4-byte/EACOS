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
  override_resource {
    target = aws_acm_certificate.agent
    values = {
      arn = "arn:aws:acm:us-east-1:123456789012:certificate/issued"
      domain_validation_options = [{
        domain_name           = "helpdesk.acme.com"
        resource_record_name  = "_x.helpdesk.acme.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_y.acm-validations.aws."
      }]
    }
  }
  override_resource {
    target = aws_lb.agent
    values = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/x/1", arn_suffix = "app/x/1", dns_name = "x.elb.amazonaws.com", zone_id = "Z35SXDOTRQ7X7K" }
  }
}

variables {
  image_tag            = "3f9c2ab"
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

run "latest_tag_rejected" {
  command = plan
  variables {
    image_tag = "latest"
  }
  expect_failures = [var.image_tag]
}

run "no_certificate_and_no_zone_fails" {
  command = plan
  variables {
    acm_certificate_arn = ""
  }
  expect_failures = [aws_acm_certificate.agent]
}

run "route53_issues_validates_and_aliases" {
  command = plan
  variables {
    acm_certificate_arn = ""
    route53_zone_id     = "Z0123456789ABC"
  }
  assert {
    condition     = length(aws_acm_certificate.agent) == 1 && length(aws_route53_record.cert_validation) == 1
    error_message = "certificate not issued and validated"
  }
  assert {
    condition     = length(aws_acm_certificate_validation.agent) == 1
    error_message = "listener would not wait for a validated certificate"
  }
  assert {
    condition     = length(aws_route53_record.app) == 1 && aws_route53_record.app[0].name == "helpdesk.acme.com"
    error_message = "DNS alias not created"
  }
}

run "existing_certificate_needs_no_dns" {
  command = plan
  assert {
    condition     = length(aws_acm_certificate.agent) == 0 && length(aws_route53_record.app) == 0
    error_message = "should reuse the provided certificate"
  }
}

run "alarms_and_waf_are_opt_in" {
  command = plan
  variables {
    alarm_email = "ops@msp.com"
    enable_waf  = true
  }
  assert {
    condition     = length(aws_sns_topic.alarms) == 1 && length(aws_cloudwatch_metric_alarm.guarantees) == 3
    error_message = "alarms missing"
  }
  assert {
    condition     = length(aws_wafv2_web_acl.agent) == 1 && length(aws_wafv2_web_acl_association.agent) == 1
    error_message = "WAF missing"
  }
}

run "defaults_create_no_alarms_or_waf" {
  command = plan
  assert {
    condition     = length(aws_sns_topic.alarms) == 0 && length(aws_wafv2_web_acl.agent) == 0
    error_message = "opt-in resources created by default"
  }
}
