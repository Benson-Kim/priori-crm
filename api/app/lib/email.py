import logging
from datetime import datetime
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.common.exceptions import EmailDeliveryException
from app.lib.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """AWS SES email client for sending transactional emails.

    The boto3 SES client is built lazily on first use so importing
    this module never requires AWS configuration; retries and the dev-mode
    skip both live on ``_send`` so every send path benefits.
    """

    def __init__(self) -> None:
        self._client_instance = None
        self._sender = settings.SES_SENDER_EMAIL

    @property
    def _client(self):
        """Lazily construct (and cache) the boto3 SES client."""
        if self._client_instance is None:
            self._client_instance = boto3.client(
                "ses",
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            )
        return self._client_instance

    def send_otp(self, recipient: str, otp_code: str) -> dict[str, Any]:
        """Send a 6-digit OTP code to the given email address."""
        subject = f"{settings.APP_NAME} — Verification Code"
        body_html = self._build_otp_html(otp_code)
        body_text = (
            f"Your verification code is: {otp_code}\n\nThis code expires in 5 minutes."
        )

        return self._send(
            recipient=recipient,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )

    def send_document_email(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> dict[str, Any]:
        """Send a transactional document email (invoice, quote, statement)."""
        html = body_html or f"<pre>{body_text}</pre>"
        return self._send(
            recipient=recipient,
            subject=subject,
            body_html=html,
            body_text=body_text,
        )

    @retry(
        stop=stop_after_attempt(settings.SES_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(ClientError),
        reraise=True,
    )
    def _send(
        self,
        recipient: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> dict[str, Any]:
        """Send an email via AWS SES.

        In development without SES credentials the send is logged and
        skipped so local flows never fail on missing AWS config.
        Transient SES ``ClientError``s are retried.
        """
        if settings.ENVIRONMENT == "development" and not settings.AWS_ACCESS_KEY_ID:
            logger.warning(
                "DEV MODE — email to %s not sent (SES not configured). Subject: %s",
                recipient,
                subject,
            )
            return {"MessageId": "dev-mode-skipped", "recipient": recipient}

        try:
            response = self._client.send_email(
                Source=self._sender,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": body_html, "Charset": "UTF-8"},
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                    },
                },
            )

            logger.info(
                "Email sent successfully",
                extra={
                    "recipient": recipient,
                    "message_id": response["MessageId"],
                    "subject": subject,
                },
            )

            return response

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))

            logger.error(
                "SES email delivery failed",
                exc_info=e,
                extra={
                    "recipient": recipient,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )

            raise EmailDeliveryException(
                detail=f"Failed to send email: {error_message}",
                recipient=recipient,
            ) from e

    @staticmethod
    def _build_otp_html(otp_code: str) -> str:
        """Build the HTML template for OTP emails."""
        digits = "".join(
            f'<span style="display:inline-block;width:40px;height:48px;'
            f"line-height:48px;text-align:center;font-size:24px;font-weight:700;"
            f"border:1px solid #E5E7EB;border-radius:8px;margin:0 4px;"
            f'background:#fff;color:#1A1A2E;">{d}</span>'
            for d in otp_code
        )

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0;padding:0;font-family:Inter,system-ui,sans-serif;">
            <div style="max-width:480px;margin:0 auto;padding:32px;">
                <h2 style="color:#1A1A2E;margin-bottom:8px;font-size:24px;">
                    Verify it's you
                </h2>
                <p style="color:#6B7280;margin-bottom:24px;font-size:14px;">
                    Use the code below to complete your sign-in to {settings.APP_NAME}.
                </p>
                <div style="text-align:center;margin:24px 0;">
                    {digits}
                </div>
                <p style="color:#6B7280;font-size:13px;margin-top:24px;">
                    This code expires in 5 minutes. If you didn't request this,
                    please ignore this email.
                </p>
                <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0;">
                <p style="color:#9CA3AF;font-size:12px;text-align:center;">
                    {settings.APP_NAME} &copy; {datetime.now().year}
                </p>
            </div>
        </body>
        </html>
        """


@lru_cache
def get_email_service() -> EmailService:
    """Return a process-wide EmailService (SES client built lazily on use)."""
    return EmailService()


class _EmailServiceProxy:
    """Backward-compatible module-level proxy.

    Existing call sites do ``from app.lib.email import email_service`` and
    call methods on it. This proxy defers to the cached instance so no SES
    client is constructed at import time while keeping that API.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_email_service(), name)


email_service = _EmailServiceProxy()
