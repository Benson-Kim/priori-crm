"""email retry on _send, dev-skip, lazy client."""

import pytest
from botocore.exceptions import ClientError
from tenacity import wait_none

import app.lib.email as email_module
from app.lib.email import EmailService


def test_ses_client_is_lazy_not_built_on_init():
    svc = EmailService()
    # Constructing the service must not build the boto3 client yet.
    assert svc._client_instance is None


def test_dev_mode_skips_send_without_credentials(monkeypatch):
    monkeypatch.setattr(
        email_module.settings, "ENVIRONMENT", "development", raising=False
    )
    monkeypatch.setattr(email_module.settings, "AWS_ACCESS_KEY_ID", "", raising=False)
    svc = EmailService()
    # Both send paths route through _send and must skip cleanly in dev.
    otp_result = svc.send_otp("user@example.com", "123456")
    doc_result = svc.send_document_email("user@example.com", "Subject", "Body")
    assert otp_result["MessageId"] == "dev-mode-skipped"
    assert doc_result["MessageId"] == "dev-mode-skipped"
    # Skipping means the SES client was never constructed.
    assert svc._client_instance is None


def test_send_retries_transient_error_using_call_time_setting(monkeypatch):
    # The retry count is read from settings at call time (not frozen
    # by an import-time decorator), so a changed SES_MAX_RETRIES takes effect
    # without a process restart. Force a non-dev send and a persistently
    # failing single-attempt; _send must call it exactly SES_MAX_RETRIES times.
    monkeypatch.setattr(
        email_module.settings, "ENVIRONMENT", "production", raising=False
    )
    monkeypatch.setattr(
        email_module.settings, "AWS_ACCESS_KEY_ID", "key", raising=False
    )
    monkeypatch.setattr(email_module.settings, "SES_MAX_RETRIES", 2, raising=False)
    # Avoid real backoff sleeps: make the per-call Retrying controller wait 0s.
    monkeypatch.setattr(
        email_module, "wait_exponential", lambda *a, **k: wait_none(), raising=False
    )

    svc = EmailService()
    calls = {"n": 0}

    def _always_fails(*_args, **_kwargs):
        calls["n"] += 1
        raise ClientError(
            {"Error": {"Code": "Throttling", "Message": "slow down"}}, "SendEmail"
        )

    monkeypatch.setattr(svc, "_send_once", _always_fails)

    with pytest.raises(ClientError):
        svc._send("user@example.com", "Subject", "<p>Body</p>", "Body")

    assert calls["n"] == 2


def test_send_otp_has_no_retry_wrapper_of_its_own():
    # Retries live only on the shared _send path, never directly on send_otp.
    assert not hasattr(EmailService.send_otp, "retry")
