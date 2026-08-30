"""Operator-configurable internal email domains.

Self-hosters (and dogfood instances) must be able to grant their own team
free Pro via `INTERNAL_EMAIL_DOMAINS` without editing source. The gate under
test is the pro path in `is_pro` (via `is_internal_email`) and the same list
in the admin analytics filter — one config source feeds both.
"""

from backend.config import settings
from backend.services import admin_analytics_service, billing_service


def test_default_domains_are_the_company_domains():
    assert billing_service.internal_email_domains() == {"ferganalabs.com", "joinstash.ai"}


def test_env_value_replaces_defaults_and_is_normalized(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_EMAIL_DOMAINS", " Progis.sk , joinstash.ai ")
    assert billing_service.internal_email_domains() == {"progis.sk", "joinstash.ai"}
    assert billing_service.is_internal_email("boss@PROGIS.sk")
    assert not billing_service.is_internal_email("boss@ferganalabs.com")


def test_empty_setting_grants_nobody(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_EMAIL_DOMAINS", "")
    assert not billing_service.is_internal_email("boss@joinstash.ai")


def test_kill_switch_still_beats_domain_match(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_DOMAINS_FREE_PRO", False)
    assert not billing_service.is_internal_email("boss@joinstash.ai")


def test_admin_analytics_filter_follows_the_same_setting(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_EMAIL_DOMAINS", "progis.sk")
    sql = admin_analytics_service.internal_filter_sql("e.user_id", exclude_internal=True)
    assert "'progis.sk'" in sql
    assert "ferganalabs.com" not in sql
