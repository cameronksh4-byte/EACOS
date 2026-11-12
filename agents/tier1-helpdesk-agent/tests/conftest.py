import os

import pytest

TEST_ENV = {
    "TENANT_ID": "acme-corp",
    "WEBHOOK_SECRET": "test-webhook-secret",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-key",
    "VOYAGE_API_KEY": "voyage-key",
    "SLACK_SIGNING_SECRET": "slack-secret",
    "JIRA_WEBHOOK_SECRET": "jira-secret",
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_ESCALATION_CHANNEL": "C-ESCALATIONS",
}
os.environ.update(TEST_ENV)


@pytest.fixture(autouse=True)
def _fresh_settings():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


from tests.fakes import env  # noqa: E402,F401  (shared fixture: fake Supabase/Claude/Voyage/n8n)
