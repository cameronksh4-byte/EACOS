"""Turn deploy/deploy.env into Terraform variables, after validating it the way the app will.

    python deploy/render_config.py deploy/deploy.env --out /path/to/vars.tfvars.json
    python deploy/render_config.py deploy/deploy.env --check        # validate only
    python deploy/render_config.py --generate-secrets               # print fresh random secrets

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
    problems: list[str] = []
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
    args = parser.parse_args(argv)

    if args.generate_secrets:
        print(generate_secrets())
        return 0
    if not args.env_file:
        parser.error("env_file is required")
    cfg = parse_env_file(args.env_file)
    if args.get:
        print(cfg.get(args.get, ""))
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
