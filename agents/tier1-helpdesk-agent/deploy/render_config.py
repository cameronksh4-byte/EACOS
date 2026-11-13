"""Turn deploy/deploy.env into Terraform variables, after validating it the way the app will.

    python deploy/render_config.py deploy/deploy.env --out /path/to/vars.tfvars.json
    python deploy/render_config.py deploy/deploy.env --check        # validate only
    python deploy/render_config.py --generate-secrets               # print fresh random secrets
    python deploy/render_config.py deploy/deploy.env --n8n-env deploy/acme-corp.n8n.env
                                                                    # env file for your n8n instance

The same `Settings` class the service uses at startup checks the configuration, so
a missing tier requirement fails here, before anything is deployed. Values that
look secret go to Secrets Manager (integration_secrets). Everything else becomes
plain container environment (extra_environment).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# deploy.env key -> Terraform variable (top-level inputs)
TF_VARS = {
    "AWS_REGION": "aws_region",
    "CLIENT_SLUG": "client_slug",
    "PUBLIC_HOSTNAME": "public_hostname",
    "ACM_CERTIFICATE_ARN": "acm_certificate_arn",
    "ROUTE53_ZONE_ID": "route53_zone_id",
    "SERVICE_TIER": "service_tier",
    "TRIAL_EXPIRES_ON": "trial_expires_on",
    "MONTHLY_TOKEN_BUDGET_USD": "monthly_token_budget_usd",
    "LLM_PROVIDER": "llm_provider",
    "ALARM_EMAIL": "alarm_email",
}
TF_SECRETS = {  # sensitive top-level Terraform variables
    "SUPABASE_URL": "supabase_url",
    "SUPABASE_SERVICE_KEY": "supabase_service_key",
    "VOYAGE_API_KEY": "voyage_api_key",
    "WEBHOOK_SECRET": "webhook_secret",
    "ANTHROPIC_API_KEY": "llm_api_key",
}
TF_LISTS = {"DISABLED_FEATURES": "disabled_features", "ALLOWED_TENANT_IDS": "allowed_tenant_ids",
            "ALLOWED_INGRESS_CIDRS": "allowed_ingress_cidrs", "BEDROCK_MODEL_ARNS": "bedrock_model_arns"}
TF_NUMBERS = {"DESIRED_COUNT": "desired_count", "MAX_COUNT": "max_count"}
TF_BOOLS = {"ENABLE_WAF": "enable_waf"}
# Integration settings forwarded to the container.
INTEGRATION_SECRETS = [
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "TEAMS_OUTGOING_WEBHOOK_SECRET", "TEAMS_INCOMING_WEBHOOK_URL",
    "JIRA_API_TOKEN", "JIRA_WEBHOOK_SECRET", "N8N_OUTBOUND_SIGNING_SECRET", "N8N_CALLBACK_SECRET",
    "GRAPH_CLIENT_SECRET", "OKTA_API_TOKEN",
]
EXTRA_ENVIRONMENT = [
    "SLACK_ESCALATION_CHANNEL", "N8N_APPROVAL_WEBHOOK_URL", "N8N_NOTIFY_WEBHOOK_URL", "IDENTITY_PROVIDER",
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "OKTA_DOMAIN", "IT_MANAGER_EMAIL", "HELPDESK_NAME", "TIER2_CONTACT_HINT",
    "JIRA_BASE_URL", "JIRA_EMAIL", "ATTACHMENT_ALLOWED_HOSTS", "TENANT_BUDGET_OVERRIDES_USD",
    "TRIAGE_MODEL", "VISION_MODEL", "REASONING_MODEL", "FLYWHEEL_MODEL",
]
# Deploy-script-only keys (not sent to Terraform).
DEPLOY_ONLY = {"SUPABASE_DB_URL", "SOP_DIR", "CATALOG_CSV", "DIRECTORY_CSV", "N8N_API_URL", "N8N_API_KEY",
               "N8N_PUBLIC_URL", "N8N_SLACK_ALERT_CHANNEL", "N8N_SLACK_SOP_REVIEW_CHANNEL", "N8N_SLACK_EXEC_CHANNEL",
               "N8N_PROVISIONING_PROVIDER", "N8N_GRAPH_CLIENT_ID", "N8N_GRAPH_CLIENT_SECRET",
               "TF_STATE_BUCKET", "IMAGE_TAG"}

_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; # comments; optional single/double quotes. Never executes anything."""
    values: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            raise SystemExit(f"{path}:{lineno}: expected KEY=value")
        key, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


# n8n webhook paths of the bundled workflows (integrations/n8n/workflows/).
N8N_APPROVALS_PATH = "/webhook/helpdesk-approvals"
N8N_NOTIFY_PATH = "/webhook/helpdesk-notify"
N8N_SLACK_ACTIONS_PATH = "/webhook/helpdesk-slack-actions"


def effective(cfg: dict[str, str]) -> dict[str, str]:
    """Fill values derived from others (e.g. n8n webhook URLs from N8N_PUBLIC_URL)."""
    out = dict(cfg)
    base = out.get("N8N_PUBLIC_URL", "").rstrip("/")
    if base:
        out.setdefault("N8N_APPROVAL_WEBHOOK_URL", "")
        out.setdefault("N8N_NOTIFY_WEBHOOK_URL", "")
        if not out["N8N_APPROVAL_WEBHOOK_URL"] and out.get("SERVICE_TIER", "TIER_0_INCENTIVE") != "TIER_0_INCENTIVE":
            out["N8N_APPROVAL_WEBHOOK_URL"] = base + N8N_APPROVALS_PATH
        if not out["N8N_NOTIFY_WEBHOOK_URL"]:
            out["N8N_NOTIFY_WEBHOOK_URL"] = base + N8N_NOTIFY_PATH
    return out


def _publicly_reachable_url_problem(name: str, url: str) -> str | None:
    """The agent runs in AWS, so n8n URLs it calls must be public HTTPS (a tunnel for local n8n)."""
    import ipaddress
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return f"{name} must be an https:// URL that AWS can reach (got {url!r})"
    local = host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home", ".localhost"))
    try:
        ip = ipaddress.ip_address(host)
        local = local or ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    if local or "." not in host:
        return (f"{name} points at {host}, which the agent in AWS can't reach. Expose your local n8n's "
                f"/webhook/ paths through a tunnel (integrations/n8n/LOCAL_N8N.md) and use that public URL")
    return None


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def isolated_settings(env: dict[str, str]):
    """The app's Settings built from `env` only (not this shell's environment or .env)."""
    from pydantic import AliasChoices

    from app.config import Settings

    class _Isolated(Settings):
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                       file_secret_settings):
            return (init_settings,)  # ignore this shell's environment, .env files and secrets dirs

    kwargs: dict = {}
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        candidates = list(alias.choices) if isinstance(alias, AliasChoices) else [name.upper()]
        key = next((c for c in candidates if c in env), None)
        if key is None:
            continue
        value: object = env[key]
        if isinstance(field.default, dict):
            value = json.loads(env[key])
        kwargs[candidates[0] if isinstance(alias, AliasChoices) else name] = value
    return _Isolated(**kwargs)


def validate(cfg: dict[str, str]) -> list[str]:
    """Problems that would stop the deploy or the service. Empty list = good to go."""
    cfg = effective(cfg)
    problems: list[str] = []
    for name in ("N8N_APPROVAL_WEBHOOK_URL", "N8N_NOTIFY_WEBHOOK_URL", "N8N_PUBLIC_URL"):
        if cfg.get(name):
            problem = _publicly_reachable_url_problem(name, cfg[name])
            if problem:
                problems.append(problem)
    for key in ("AWS_REGION", "CLIENT_SLUG", "PUBLIC_HOSTNAME", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                "VOYAGE_API_KEY", "WEBHOOK_SECRET"):
        if not cfg.get(key):
            problems.append(f"{key} is required")
    if cfg.get("CLIENT_SLUG") and not re.fullmatch(r"[a-z0-9-]{2,24}", cfg["CLIENT_SLUG"]):
        problems.append("CLIENT_SLUG must be 2-24 lowercase letters, digits or dashes")
    if not cfg.get("ACM_CERTIFICATE_ARN") and not cfg.get("ROUTE53_ZONE_ID"):
        problems.append("set ROUTE53_ZONE_ID (automatic certificate + DNS) or ACM_CERTIFICATE_ARN")
    tier = cfg.get("SERVICE_TIER", "TIER_0_INCENTIVE")
    trial = cfg.get("TRIAL_EXPIRES_ON", "")
    if tier == "TIER_0_INCENTIVE" and not trial:
        problems.append("TIER_0 needs TRIAL_EXPIRES_ON (YYYY-MM-DD, e.g. 30 days out) or TRIAL_EXPIRES_ON=none")
    if trial and trial != "none":
        try:
            date.fromisoformat(trial)
        except ValueError:
            problems.append("TRIAL_EXPIRES_ON must be YYYY-MM-DD or none")
    if cfg.get("WEBHOOK_SECRET") and len(cfg["WEBHOOK_SECRET"]) < 24:
        problems.append("WEBHOOK_SECRET should be at least 24 random characters (use --generate-secrets)")
    if cfg.get("LLM_PROVIDER", "anthropic") == "anthropic" and not cfg.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")

    # The service's own startup check, on exactly the environment the container will get.
    container_env = {k: v for k, v in cfg.items() if k not in DEPLOY_ONLY and v != ""}
    container_env.update({
        "TENANT_ID": cfg.get("CLIENT_SLUG", ""),
        "PUBLIC_BASE_URL": f"https://{cfg.get('PUBLIC_HOSTNAME', 'example.invalid')}",
    })
    if trial == "none":
        container_env.pop("TRIAL_EXPIRES_ON", None)
    if cfg.get("LLM_PROVIDER", "anthropic") != "anthropic":
        container_env.pop("ANTHROPIC_API_KEY", None)
    try:
        settings = isolated_settings(container_env)
    except Exception as exc:  # noqa: BLE001 - pydantic errors are informative
        problems.append(f"configuration rejected by the app: {exc}")
    else:
        problems += [f"{m} is required at {settings.service_tier.value}" for m in settings.missing_core_settings()]
    return problems


def render(cfg: dict[str, str], image_tag: str) -> dict:
    cfg = effective(cfg)
    out: dict = {"image_tag": image_tag}
    for key, var in {**TF_VARS, **TF_SECRETS}.items():
        if cfg.get(key):
            out[var] = cfg[key]
    if out.get("trial_expires_on") == "none":
        out["trial_expires_on"] = ""
    for key, var in TF_LISTS.items():
        if cfg.get(key):
            out[var] = _csv(cfg[key])
    for key, var in TF_NUMBERS.items():
        if cfg.get(key):
            out[var] = int(cfg[key])
    for key, var in TF_BOOLS.items():
        if cfg.get(key):
            out[var] = cfg[key].strip().lower() in ("true", "1", "yes")
    out["integration_secrets"] = {k: cfg[k] for k in INTEGRATION_SECRETS if cfg.get(k)}
    out["extra_environment"] = {k: cfg[k] for k in EXTRA_ENVIRONMENT if cfg.get(k)}
    if cfg.get("LLM_PROVIDER") == "bedrock":
        out.pop("llm_api_key", None)
    return out


def n8n_environment(cfg: dict[str, str]) -> dict[str, str]:
    """Environment for the n8n instance, with every shared secret taken from deploy.env."""
    cfg = effective(cfg)
    alert = cfg.get("N8N_SLACK_ALERT_CHANNEL") or cfg.get("SLACK_ESCALATION_CHANNEL", "")
    provider = cfg.get("N8N_PROVISIONING_PROVIDER") or {"microsoft_graph": "entra", "okta": "okta"}.get(
        cfg.get("IDENTITY_PROVIDER", ""), "none")
    tenants = ",".join(dict.fromkeys([cfg.get("CLIENT_SLUG", ""), *_csv(cfg.get("ALLOWED_TENANT_IDS", ""))]))
    env = {
        # Required by the workflows' Code nodes.
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": "false",
        "NODE_FUNCTION_ALLOW_BUILTIN": "crypto",
        # Public base URL of this n8n (the tunnel), so n8n shows the right webhook URLs.
        "WEBHOOK_URL": cfg.get("N8N_PUBLIC_URL", "").rstrip("/") + "/" if cfg.get("N8N_PUBLIC_URL") else "",
        # Shared with the agent. These must match deploy.env exactly.
        "N8N_AGENT_SIGNING_SECRET": cfg.get("N8N_OUTBOUND_SIGNING_SECRET", ""),
        "N8N_CALLBACK_SECRET": cfg.get("N8N_CALLBACK_SECRET", ""),
        "AGENT_BASE_URL": f"https://{cfg.get('PUBLIC_HOSTNAME', '')}",
        "AGENT_WEBHOOK_SECRET": cfg.get("WEBHOOK_SECRET", ""),
        "AGENT_TENANTS": tenants,
        "SLACK_BOT_TOKEN": cfg.get("SLACK_BOT_TOKEN", ""),
        "SLACK_SIGNING_SECRET": cfg.get("SLACK_SIGNING_SECRET", ""),
        "SLACK_ALERT_CHANNEL": alert,
        "SLACK_SOP_REVIEW_CHANNEL": cfg.get("N8N_SLACK_SOP_REVIEW_CHANNEL") or alert,
        "SLACK_EXEC_CHANNEL": cfg.get("N8N_SLACK_EXEC_CHANNEL") or alert,
        "PROVISIONING_PROVIDER": provider,
    }
    if provider == "entra":
        env["GRAPH_TENANT_ID"] = cfg.get("GRAPH_TENANT_ID", "")
        env["GRAPH_CLIENT_ID"] = cfg.get("N8N_GRAPH_CLIENT_ID") or cfg.get("GRAPH_CLIENT_ID", "")
        env["GRAPH_CLIENT_SECRET"] = cfg.get("N8N_GRAPH_CLIENT_SECRET") or cfg.get("GRAPH_CLIENT_SECRET", "")
    if provider == "okta":
        env["OKTA_DOMAIN"] = cfg.get("OKTA_DOMAIN", "")
        env["OKTA_API_TOKEN"] = cfg.get("OKTA_API_TOKEN", "")
    return env


def n8n_env_problems(env: dict[str, str]) -> list[str]:
    required = ["N8N_AGENT_SIGNING_SECRET", "N8N_CALLBACK_SECRET", "AGENT_WEBHOOK_SECRET", "SLACK_BOT_TOKEN",
                "SLACK_SIGNING_SECRET", "SLACK_ALERT_CHANNEL", "WEBHOOK_URL"]
    if env.get("PROVISIONING_PROVIDER") == "entra":
        required += ["GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"]
    if env.get("PROVISIONING_PROVIDER") == "okta":
        required += ["OKTA_DOMAIN", "OKTA_API_TOKEN"]
    source = {"N8N_AGENT_SIGNING_SECRET": "N8N_OUTBOUND_SIGNING_SECRET", "AGENT_WEBHOOK_SECRET": "WEBHOOK_SECRET",
              "SLACK_ALERT_CHANNEL": "SLACK_ESCALATION_CHANNEL or N8N_SLACK_ALERT_CHANNEL",
              "WEBHOOK_URL": "N8N_PUBLIC_URL"}
    return [f"n8n needs {k} (set {source.get(k, k)} in deploy.env)" for k in required if not env.get(k)]


def generate_secrets() -> str:
    names = ["WEBHOOK_SECRET", "N8N_OUTBOUND_SIGNING_SECRET", "N8N_CALLBACK_SECRET"]
    return "\n".join(f"{n}={secrets.token_urlsafe(32)}" for n in names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("env_file", nargs="?", type=Path)
    parser.add_argument("--out", type=Path, help="write Terraform variables JSON here (mode 600)")
    parser.add_argument("--image-tag", default="unset")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--generate-secrets", action="store_true")
    parser.add_argument("--get", metavar="KEY", help="print one value (for deploy.sh)")
    parser.add_argument("--n8n-env", type=Path, metavar="FILE",
                        help="write the environment file for your n8n instance (mode 600)")
    args = parser.parse_args(argv)

    if args.generate_secrets:
        print(generate_secrets())
        return 0
    if not args.env_file:
        parser.error("env_file is required")
    cfg = parse_env_file(args.env_file)
    if args.get:
        print(effective(cfg).get(args.get, ""))
        return 0
    if args.n8n_env:
        env = n8n_environment(cfg)
        problems = n8n_env_problems(env)
        fd = os.open(args.n8n_env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("# Environment for the help desk n8n workflows, generated from deploy.env by\n"
                    "# deploy/render_config.py --n8n-env. Load it into n8n and restart n8n.\n")
            f.writelines(f"{k}={v}\n" for k, v in env.items())
        for problem in problems:
            print(f"warning: {problem}", file=sys.stderr)
        return 0
    problems = validate(cfg)
    if problems:
        print("deploy.env has problems:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    if args.check:
        tier = cfg.get("SERVICE_TIER", "TIER_0_INCENTIVE")
        print(f"deploy.env ok: {cfg['CLIENT_SLUG']} at {tier} -> https://{cfg['PUBLIC_HOSTNAME']}")
        return 0
    if args.out:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(render(cfg, args.image_tag), f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
