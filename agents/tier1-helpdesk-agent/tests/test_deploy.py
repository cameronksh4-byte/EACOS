"""Deploy kit: render_config validation/rendering, and deploy.sh control flow against stubs.

deploy.sh runs for real, with `aws`, `terraform` and `docker` replaced by stubs that
log every call, so the step order, arguments and secret handling are tested
without an AWS account.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import render_config  # noqa: E402

SECRET_VALUES = {
    "SUPABASE_SERVICE_KEY": "sb-service-key-SECRET",
    "VOYAGE_API_KEY": "voyage-SECRET",
    "ANTHROPIC_API_KEY": "sk-ant-SECRET",
    "WEBHOOK_SECRET": "webhook-secret-0123456789-SECRET",
    "SLACK_BOT_TOKEN": "xoxb-SECRET",
}
TIER0 = {
    "AWS_REGION": "us-east-1", "CLIENT_SLUG": "acme-corp", "PUBLIC_HOSTNAME": "helpdesk.acme.com",
    "ROUTE53_ZONE_ID": "Z0123", "SERVICE_TIER": "TIER_0_INCENTIVE", "TRIAL_EXPIRES_ON": "2026-12-31",
    "SUPABASE_URL": "https://x.supabase.co", "LLM_PROVIDER": "anthropic", "SLACK_ESCALATION_CHANNEL": "C-ESC",
    "MONTHLY_TOKEN_BUDGET_USD": "25", **SECRET_VALUES,
}


def write_env(path: Path, values: dict[str, str]) -> Path:
    path.write_text("# test\n" + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    path.chmod(0o600)
    return path


# =============================================================================
# render_config
# =============================================================================


def test_example_file_parses_and_lists_every_key_render_knows():
    cfg = render_config.parse_env_file(ROOT / "deploy" / "deploy.env.example")
    known = (set(render_config.TF_VARS) | set(render_config.TF_SECRETS) | set(render_config.TF_LISTS)
             | set(render_config.TF_NUMBERS) | set(render_config.TF_BOOLS) | set(render_config.INTEGRATION_SECRETS)
             | set(render_config.EXTRA_ENVIRONMENT) | render_config.DEPLOY_ONLY)
    unknown = set(cfg) - known
    assert not unknown, f"deploy.env.example keys render_config ignores: {unknown}"


def test_parser_handles_quotes_comments_and_never_executes(tmp_path):
    f = tmp_path / "x.env"
    f.write_text('A=plain\nB="with spaces # not a comment"\nC=value # comment\nD=$(touch pwned)\n')
    cfg = render_config.parse_env_file(f)
    assert cfg == {"A": "plain", "B": "with spaces # not a comment", "C": "value", "D": "$(touch pwned)"}
    assert not (tmp_path / "pwned").exists()


def test_tier0_minimal_config_is_valid():
    assert render_config.validate(dict(TIER0)) == []


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"ROUTE53_ZONE_ID": ""}, "ROUTE53_ZONE_ID"),
        ({"TRIAL_EXPIRES_ON": ""}, "TRIAL_EXPIRES_ON"),
        ({"TRIAL_EXPIRES_ON": "next month"}, "YYYY-MM-DD"),
        ({"WEBHOOK_SECRET": "short"}, "24 random"),
        ({"ANTHROPIC_API_KEY": ""}, "ANTHROPIC_API_KEY"),
        ({"CLIENT_SLUG": "Acme Corp"}, "CLIENT_SLUG"),
        ({"SLACK_ESCALATION_CHANNEL": ""}, "SLACK_ESCALATION_CHANNEL"),       # outage channel (app rule)
        ({"SERVICE_TIER": "TIER_1_PRODUCTION"}, "N8N_APPROVAL_WEBHOOK_URL"),  # app's tier rule
        ({"SERVICE_TIER": "TIER_9"}, "rejected by the app"),
        ({"DISABLED_FEATURES": "telepathy"}, "rejected by the app"),
    ],
)
def test_invalid_configs_are_caught_before_deploying(change, expected):
    problems = render_config.validate({**TIER0, **change})
    assert any(expected in p for p in problems), problems


def test_validation_ignores_the_operators_shell_environment(monkeypatch):
    monkeypatch.setenv("SERVICE_TIER", "TIER_2_MANAGED_OPS")  # would demand n8n settings if it leaked
    monkeypatch.setenv("DISABLED_FEATURES", "telepathy")
    assert render_config.validate(dict(TIER0)) == []


def test_tier1_complete_config_is_valid():
    cfg = {**TIER0, "SERVICE_TIER": "TIER_1_PRODUCTION", "N8N_APPROVAL_WEBHOOK_URL": "https://n8n.x/webhook/a",
           "N8N_OUTBOUND_SIGNING_SECRET": "o" * 32, "N8N_CALLBACK_SECRET": "c" * 32}
    assert render_config.validate(cfg) == []


def test_bedrock_needs_no_anthropic_key_and_drops_it():
    cfg = {**TIER0, "LLM_PROVIDER": "bedrock", "ANTHROPIC_API_KEY": ""}
    assert render_config.validate(cfg) == []
    assert "llm_api_key" not in render_config.render(cfg, "abc123")


def test_render_routes_secrets_to_secrets_manager_and_config_to_environment():
    cfg = {**TIER0, "JIRA_BASE_URL": "https://acme.atlassian.net", "JIRA_API_TOKEN": "jira-SECRET",
           "DISABLED_FEATURES": "outage_detection, screenshot_vision", "ENABLE_WAF": "true", "MAX_COUNT": "6",
           "TRIAL_EXPIRES_ON": "none"}
    out = render_config.render(cfg, "3f9c2ab1d2e4")
    assert out["image_tag"] == "3f9c2ab1d2e4" and out["trial_expires_on"] == ""
    assert out["integration_secrets"] == {"SLACK_BOT_TOKEN": "xoxb-SECRET", "JIRA_API_TOKEN": "jira-SECRET"}
    assert out["extra_environment"] == {"SLACK_ESCALATION_CHANNEL": "C-ESC", "JIRA_BASE_URL": "https://acme.atlassian.net"}
    assert out["llm_api_key"] == "sk-ant-SECRET" and out["webhook_secret"] == SECRET_VALUES["WEBHOOK_SECRET"]
    assert out["disabled_features"] == ["outage_detection", "screenshot_vision"]
    assert out["enable_waf"] is True and out["max_count"] == 6
    assert not any("SECRET" in v for v in out["extra_environment"].values())


def test_rendered_file_is_private(tmp_path):
    env = write_env(tmp_path / "c.env", TIER0)
    out = tmp_path / "vars.json"
    assert render_config.main([str(env), "--out", str(out), "--image-tag", "t1"]) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert json.loads(out.read_text())["client_slug"] == "acme-corp"


def test_generated_secrets_are_long_and_distinct(capsys):
    render_config.main(["--generate-secrets"])
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())
    assert set(lines) == {"WEBHOOK_SECRET", "N8N_OUTBOUND_SIGNING_SECRET", "N8N_CALLBACK_SECRET"}
    assert all(len(v) >= 40 for v in lines.values()) and len(set(lines.values())) == 3


# =============================================================================
# deploy.sh against stubbed aws / terraform / docker
# =============================================================================

STUB = r'''#!/usr/bin/env bash
# Stub for {name}: log the call, answer like the real tool would.
printf '%s\t%s\n' "{name}" "$*" >> "$STUB_LOG"
args="$*"
case "{name}" in
  aws)
    case "$args" in
      *"sts get-caller-identity --query Account"*) echo 123456789012 ;;
      *"sts get-caller-identity --query Arn"*) echo arn:aws:iam::123456789012:user/deployer ;;
      *"s3api head-bucket"*) [[ -f "$STUB_STATE/bucket" ]] || exit 1 ;;
      *"s3api create-bucket"*) touch "$STUB_STATE/bucket"; echo '{}' ;;
      *"ecr describe-images"*) exit 1 ;;
      *"ecr get-login-password"*) echo pw ;;
    esac ;;
  terraform)
    case "$args" in
      *"version -json"*) echo '{"terraform_version":"1.17.2"}' ;;
      *"output -raw ecr_repository_url"*) echo 123456789012.dkr.ecr.us-east-1.amazonaws.com/acme-corp-tier1-agent ;;
      *"output -raw alb_dns_name"*) echo acme-alb-1.us-east-1.elb.amazonaws.com ;;
      *"-var-file="*) f="${args##*-var-file=}"; f="${f%% *}"; cp "$f" "$STUB_STATE/tfvars.json" ;;
    esac ;;
  docker)
    case "$args" in
      *"buildx inspect"*) echo "Platforms: linux/amd64, linux/arm64" ;;
      *"--password-stdin"*) cat > /dev/null ;;  # like the real docker login
    esac ;;
esac
exit 0
'''


@pytest.fixture
def stubbed(tmp_path):
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    for name in ("aws", "terraform", "docker"):
        stub = bin_dir / name
        stub.write_text(STUB.replace("{name}", name))
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    log.touch()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log), "STUB_STATE": str(state),
           "DEPLOY_VENV": sys.prefix}

    def run(values: dict[str, str], *flags: str, stdin: str = "", extra_env: dict | None = None):
        cfg = write_env(tmp_path / "client.env", values)
        proc = subprocess.run(["bash", str(ROOT / "deploy" / "deploy.sh"), str(cfg), *flags],
                              env={**env, **(extra_env or {})}, input=stdin,
                              capture_output=True, text=True, timeout=300, cwd=tmp_path)
        calls = [line.split("\t", 1) for line in log.read_text().splitlines()]
        return proc, calls, state

    return run


def _git_tag() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")
def test_full_deploy_runs_every_step_in_order(stubbed):
    proc, calls, state = stubbed({**TIER0, "SOP_DIR": ""}, "--yes", "--skip-smoke")
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    flat = [f"{tool} {args}" for tool, args in calls]

    def index(fragment: str) -> int:
        if fragment == "APPLY_SAVED_PLAN":  # the final apply of the reviewed plan file
            return next(i for i, c in enumerate(flat) if " apply -input=false " in c and c.endswith("/tfplan"))
        return next(i for i, c in enumerate(flat) if fragment in c)

    order = [
        "aws sts get-caller-identity",
        "aws s3api create-bucket --bucket tier1-agent-tfstate-123456789012-us-east-1",
        "aws s3api put-public-access-block",
        "aws s3api put-bucket-versioning",
        "aws s3api put-bucket-encryption",
        "terraform -chdir=",                                   # init
        "-target=aws_ecr_repository.agent_repo",                 # ECR first (no image/service race)
        "docker buildx build --platform linux/arm64",
        "plan -input=false -var-file=",
        "APPLY_SAVED_PLAN",
        "aws ecs wait services-stable --cluster acme-corp-cluster --services acme-corp-tier1-agent-service",
    ]
    positions = [index(o) for o in order]
    assert positions == sorted(positions), list(zip(order, positions, strict=True))

    init = flat[index("init -input=false")]
    assert "key=clients/acme-corp/terraform.tfstate" in init and "use_lockfile=true" in init and "encrypt=true" in init
    build = flat[index("docker buildx build")]
    tag = build.split(" -t ", 1)[1].split()[0].rsplit(":", 1)[1]
    assert tag.startswith(_git_tag()) and "--push" in build
    tfvars = json.loads((state / "tfvars.json").read_text())
    assert tfvars["image_tag"] == tag and tfvars["service_tier"] == "TIER_0_INCENTIVE"
    assert tfvars["integration_secrets"]["SLACK_BOT_TOKEN"] == "xoxb-SECRET"
    # Secrets travel only in the private var file, never as command-line arguments.
    for _, args in calls:
        assert not any(v in args for v in SECRET_VALUES.values()), args
    assert "Deployed" in proc.stdout and "CNAME" not in proc.stdout  # Route 53 handles DNS


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_plan_mode_changes_nothing(stubbed):
    _, _, state = stubbed({**TIER0, "SOP_DIR": ""}, "--yes", "--skip-smoke")  # creates the bucket
    proc, calls, _ = stubbed({**TIER0, "SOP_DIR": ""}, "--plan")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    last_run = calls[[i for i, (t, a) in enumerate(calls) if "sts get-caller-identity --query Account" in a][-1]:]
    flat = [f"{t} {a}" for t, a in last_run]
    assert any("plan -input=false" in c for c in flat)
    assert not any(c.startswith("docker buildx build") or " apply " in f" {c} " or "create-bucket" in c
                   or "ecs wait" in c for c in flat), flat
    assert "Plan complete" in proc.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_invalid_config_stops_before_touching_aws(stubbed):
    proc, calls, _ = stubbed({**TIER0, "SERVICE_TIER": "TIER_1_PRODUCTION"}, "--yes")
    assert proc.returncode != 0
    assert "N8N_APPROVAL_WEBHOOK_URL" in proc.stderr
    assert not any(t == "aws" for t, _ in calls), calls


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_refuses_an_env_file_tracked_by_git(stubbed, tmp_path):
    tracked = ROOT / ".env.example"  # committed, so git tracks it
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(tmp_path / 'calls.log'),
           "STUB_STATE": str(tmp_path / "state"), "DEPLOY_VENV": sys.prefix}
    proc = subprocess.run(["bash", str(ROOT / "deploy" / "deploy.sh"), str(tracked), "--plan"], env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0 and "tracked by git" in proc.stderr


def _calls_of_last_run(calls):
    start = [i for i, (_, a) in enumerate(calls) if "sts get-caller-identity --query Account" in a][-1]
    return [f"{t} {a}" for t, a in calls[start:]]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_destroy_requires_typing_the_client_slug(stubbed):
    stubbed({**TIER0, "SOP_DIR": ""}, "--yes", "--skip-smoke")  # deploy first (creates the bucket)
    proc, calls, _ = stubbed({**TIER0, "SOP_DIR": ""}, "--destroy", "--yes", stdin="acme\n")
    assert proc.returncode != 0 and "nothing was destroyed" in proc.stderr
    flat = _calls_of_last_run(calls)
    assert any("plan -destroy" in c for c in flat) and not any(c.endswith("/destroy.tfplan") and " apply " in c
                                                                   for c in flat)

    proc, calls, _ = stubbed({**TIER0, "SOP_DIR": ""}, "--destroy", stdin="acme-corp\n")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    flat = _calls_of_last_run(calls)
    assert any(" apply -input=false " in c and c.endswith("/destroy.tfplan") for c in flat)
    assert not any("docker" in c or "create-bucket" in c for c in flat)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_destroy_without_a_deployment_stops(stubbed):
    proc, calls, _ = stubbed({**TIER0, "SOP_DIR": ""}, "--destroy", extra_env={"DEPLOY_DESTROY_CONFIRM": "acme-corp"})
    assert proc.returncode != 0 and "nothing deployed to destroy" in proc.stderr
    assert not any("create-bucket" in a or "plan" in a for _, a in calls)


# =============================================================================
# Local n8n: public-URL checks, derived webhook URLs, generated n8n environment, ping
# =============================================================================

TIER1_LOCAL_N8N = {**TIER0, "SERVICE_TIER": "TIER_1_PRODUCTION", "N8N_PUBLIC_URL": "https://n8n-hooks.acme.com",
                   "N8N_OUTBOUND_SIGNING_SECRET": "o" * 40, "N8N_CALLBACK_SECRET": "c" * 40,
                   "SLACK_SIGNING_SECRET": "slack-signing-SECRET", "N8N_API_URL": "http://localhost:5678"}


def test_public_n8n_url_derives_both_webhooks_and_validates():
    assert render_config.validate(dict(TIER1_LOCAL_N8N)) == []
    eff = render_config.effective(TIER1_LOCAL_N8N)
    assert eff["N8N_APPROVAL_WEBHOOK_URL"] == "https://n8n-hooks.acme.com/webhook/helpdesk-approvals"
    assert eff["N8N_NOTIFY_WEBHOOK_URL"] == "https://n8n-hooks.acme.com/webhook/helpdesk-notify"
    out = render_config.render(TIER1_LOCAL_N8N, "t")
    assert out["extra_environment"]["N8N_APPROVAL_WEBHOOK_URL"].endswith("/webhook/helpdesk-approvals")
    assert "N8N_API_URL" not in out["extra_environment"]  # deploy-machine only, never sent to AWS


def test_tier0_with_n8n_only_derives_the_notify_webhook():
    eff = render_config.effective({**TIER0, "N8N_PUBLIC_URL": "https://n8n-hooks.acme.com"})
    assert eff["N8N_APPROVAL_WEBHOOK_URL"] == "" and eff["N8N_NOTIFY_WEBHOOK_URL"].endswith("/helpdesk-notify")


@pytest.mark.parametrize("url", ["http://localhost:5678", "https://localhost:5678", "https://192.168.1.20",
                                 "https://n8n.local", "https://10.0.0.5:5678", "http://n8n-hooks.acme.com", "https://n8n"])
def test_n8n_urls_aws_cannot_reach_are_rejected(url):
    problems = render_config.validate({**TIER1_LOCAL_N8N, "N8N_PUBLIC_URL": url})
    assert any("N8N_PUBLIC_URL" in p and ("reach" in p) for p in problems), problems


def test_local_api_url_is_fine_because_it_is_used_from_this_machine():
    assert render_config.validate({**TIER1_LOCAL_N8N, "N8N_API_URL": "http://127.0.0.1:5678"}) == []


def test_generated_n8n_env_covers_everything_the_workflows_read():
    import re as _re

    env = render_config.n8n_environment({**TIER1_LOCAL_N8N, "N8N_PROVISIONING_PROVIDER": "entra",
                                         "GRAPH_TENANT_ID": "tid", "GRAPH_CLIENT_ID": "cid",
                                         "GRAPH_CLIENT_SECRET": "graph-SECRET"})
    src = "\n".join(p.read_text() for p in (ROOT / "integrations" / "n8n" / "src").glob("*.js"))
    required = set(_re.findall(r"env\('([A-Z0-9_]+)'\)", src))  # env('X') with no fallback
    required -= {"OKTA_DOMAIN", "OKTA_API_TOKEN"}               # only for PROVISIONING_PROVIDER=okta
    missing = {k for k in required if not env.get(k)}
    assert not missing, missing
    assert env["N8N_BLOCK_ENV_ACCESS_IN_NODE"] == "false" and env["NODE_FUNCTION_ALLOW_BUILTIN"] == "crypto"
    # Shared secrets are the deploy file's, so they can't drift.
    assert env["N8N_AGENT_SIGNING_SECRET"] == TIER1_LOCAL_N8N["N8N_OUTBOUND_SIGNING_SECRET"]
    assert env["N8N_CALLBACK_SECRET"] == TIER1_LOCAL_N8N["N8N_CALLBACK_SECRET"]
    assert env["AGENT_WEBHOOK_SECRET"] == TIER1_LOCAL_N8N["WEBHOOK_SECRET"]
    assert env["AGENT_BASE_URL"] == "https://helpdesk.acme.com" and env["WEBHOOK_URL"] == "https://n8n-hooks.acme.com/"
    assert env["SLACK_ALERT_CHANNEL"] == env["SLACK_SOP_REVIEW_CHANNEL"] == "C-ESC"
    assert render_config.n8n_env_problems(env) == []


def test_provisioning_provider_follows_identity_provider():
    assert render_config.n8n_environment({**TIER1_LOCAL_N8N, "IDENTITY_PROVIDER": "okta"})["PROVISIONING_PROVIDER"] == "okta"
    env = render_config.n8n_environment(dict(TIER1_LOCAL_N8N))
    assert env["PROVISIONING_PROVIDER"] == "none" and "GRAPH_CLIENT_SECRET" not in env


def test_n8n_env_file_is_private_and_warns_on_gaps(tmp_path, capsys):
    cfg = write_env(tmp_path / "c.env", {**TIER1_LOCAL_N8N, "SLACK_SIGNING_SECRET": ""})
    out = tmp_path / "c.n8n.env"
    assert render_config.main([str(cfg), "--n8n-env", str(out)]) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    parsed = render_config.parse_env_file(out)
    assert parsed["N8N_AGENT_SIGNING_SECRET"] == "o" * 40
    assert "SLACK_SIGNING_SECRET" in capsys.readouterr().err


@pytest.mark.parametrize(
    "status, expected",
    [(200, "signature accepted"), (401, "N8N_AGENT_SIGNING_SECRET"), (404, "publish"), (403, "tunnel")],
)
def test_n8n_ping_explains_each_failure(monkeypatch, status, expected):
    import httpx

    import n8n_ping

    seen = {}

    def fake_post(url, content, timeout, headers):
        seen["sig"], seen["body"] = headers["X-Agent-Signature"], content
        return httpx.Response(status, text="x")

    monkeypatch.setattr(n8n_ping.httpx, "post", fake_post)
    ok, detail = n8n_ping.ping("https://n8n-hooks.acme.com/webhook/helpdesk-notify", "s" * 40)
    assert ok == (status == 200) and expected in detail
    from app.services import signing

    assert signing.verify("s" * 40, seen["body"], seen["sig"], 300)
    assert json.loads(seen["body"])["event"] == "deploy.ping"


def test_n8n_ping_unreachable_and_http_refused(monkeypatch):
    import httpx

    import n8n_ping

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(n8n_ping.httpx, "post", down)
    ok, detail = n8n_ping.ping("https://n8n-hooks.acme.com/webhook/x", "s")
    assert not ok and "tunnel" in detail
    ok, detail = n8n_ping.ping("http://n8n-hooks.acme.com/webhook/x", "s")
    assert not ok and "https" in detail


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_deploy_writes_the_n8n_env_file_next_to_the_config(stubbed):
    stubbed({**TIER0, "SOP_DIR": ""}, "--yes", "--skip-smoke")  # create state bucket
    proc, _, _ = stubbed({**TIER1_LOCAL_N8N, "SOP_DIR": ""}, "--plan")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    n8n_env = Path(proc.args[2]).with_name("client.n8n.env")
    assert n8n_env.exists() and stat.S_IMODE(n8n_env.stat().st_mode) == 0o600
    assert "N8N_AGENT_SIGNING_SECRET=" + "o" * 40 in n8n_env.read_text()
    assert "o" * 40 not in proc.stdout + proc.stderr  # never printed
