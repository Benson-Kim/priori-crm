"""W3.7 - email retry on _send (LIB-BE-1), dev-skip (LIB-BE-2), lazy client (LIB-BE-3)."""

import app.lib.email as email_module
from app.lib.email import EmailService


def test_ses_client_is_lazy_not_built_on_init():
    svc = EmailService()
    # Constructing the service must not build the boto3 client yet.
    assert svc._client_instance is None


def test_dev_mode_skips_send_without_credentials(monkeypatch):
    monkeypatch.setattr(email_module.settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(email_module.settings, "AWS_ACCESS_KEY_ID", "", raising=False)
    svc = EmailService()
    # Both send paths route through _send and must skip cleanly in dev.
    otp_result = svc.send_otp("user@example.com", "123456")
    doc_result = svc.send_document_email("user@example.com", "Subject", "Body")
    assert otp_result["MessageId"] == "dev-mode-skipped"
    assert doc_result["MessageId"] == "dev-mode-skipped"
    # Skipping means the SES client was never constructed.
    assert svc._client_instance is None


def test_retry_decorator_is_on_send_not_send_otp():
    # tenacity wraps the retried callable with a `.retry` attribute.
    assert hasattr(EmailService._send, "retry")
    assert not hasattr(EmailService.send_otp, "retry")
