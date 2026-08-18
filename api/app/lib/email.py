import logging
from datetime import datetime
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError
from tenacity import (
    Retrying,
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

    def send_password_reset(self, recipient: str, reset_link: str) -> dict[str, Any]:
        """Send a password-reset link to the given email address."""
        subject = f"{settings.APP_NAME} \u2014 Reset your password"
        body_html = self._build_password_reset_html(reset_link)
        body_text = (
            "We received a request to reset your password.\n\n"
            f"Open this link to choose a new password:\n{reset_link}\n\n"
            f"This link expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES} "
            "minutes. If you did not request a password reset, you can safely "
            "ignore this email \u2014 your password will not change."
        )
        return self._send(
            recipient=recipient,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )

    def send_new_device_alert(
        self, recipient: str, country: str | None = None
    ) -> dict[str, Any]:
        """Notify the account owner that a NEW device entered their baseline.

        Sent when a passed OTP step-up absorbs a never-seen (or aged-out)
        device into the user's behavioural baseline (#67 H13): absorption
        stops that device re-firing as a soft signal for
        ``RISK_BASELINE_TRUST_TTL_DAYS``, so the owner should hear about
        it — a compromised inbox (SIM swap) is exactly the scenario where
        this mail is the victim's only tell.
        """
        subject = f"{settings.APP_NAME} \u2014 New device signed in"
        location = f" from {country}" if country else ""
        body_text = (
            f"A new device{location} just completed a sign-in verification "
            f"on your {settings.APP_NAME} account and is now remembered as "
            "trusted.\n\n"
            "If this was you, no action is needed. If you do not recognise "
            "this sign-in, reset your password immediately — that signs out "
            "every session."
        )
        return self._send(
            recipient=recipient,
            subject=subject,
            body_html=f'<pre style="font-family:inherit">{body_text}</pre>',
            body_text=body_text,
        )

    def send_new_country_alert(
        self, recipient: str, country: str | None = None
    ) -> dict[str, Any]:
        """Notify the account owner that a NEW country entered their baseline.

        Sent when a passed OTP step-up absorbs a never-seen (or aged-out)
        country on an already-known device fingerprint (#67 line review
        §4). Without it, an inbox-compromise attacker replaying the
        victim's fingerprint got their location whitelisted with zero
        user-visible tell — this mail is that tell.
        """
        subject = f"{settings.APP_NAME} \u2014 Sign-in from a new location"
        location = f" ({country})" if country else ""
        body_text = (
            f"A sign-in verification on your {settings.APP_NAME} account "
            f"just completed from a new location{location}, which is now "
            "remembered as trusted.\n\n"
            "If this was you, no action is needed. If you do not recognise "
            "this sign-in, reset your password immediately — that signs out "
            "every session."
        )
        return self._send(
            recipient=recipient,
            subject=subject,
            body_html=f'<pre style="font-family:inherit">{body_text}</pre>',
            body_text=body_text,
        )

    def send_document_email(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        """Send a transactional document email (invoice, quote, statement).

        ``attachments`` is a list of ``(filename, content, mime_type)``
        tuples; when present the message is sent as raw MIME so SES can
        carry the files.
        """
        html = body_html or f"<pre>{body_text}</pre>"
        return self._send(
            recipient=recipient,
            subject=subject,
            body_html=html,
            body_text=body_text,
            attachments=attachments,
        )

    def _send(
        self,
        recipient: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        """Send an email via AWS SES.

        In development without SES credentials the send is logged and
        skipped so local flows never fail on missing AWS config.
        Transient SES ``ClientError``s are retried; the retry count is read
        from ``settings.SES_MAX_RETRIES`` per call rather than
        being frozen at import time, so configuration changes take effect
        without a process restart.
        """
        retryer = Retrying(
            stop=stop_after_attempt(settings.SES_MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type(ClientError),
            reraise=True,
        )
        return retryer(
            self._send_once, recipient, subject, body_html, body_text, attachments
        )

    def _send_once(
        self,
        recipient: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        """Perform a single SES send attempt (wrapped with retries by _send)."""
        if settings.ENVIRONMENT == "development" and not settings.AWS_ACCESS_KEY_ID:
            logger.warning(
                "DEV MODE — email to %s not sent (SES not configured). Subject: %s",
                recipient,
                subject,
            )
            return {"MessageId": "dev-mode-skipped", "recipient": recipient}

        try:
            if attachments:
                raw = self._build_raw_message(
                    sender=self._sender,
                    recipient=recipient,
                    subject=subject,
                    body_html=body_html,
                    body_text=body_text,
                    attachments=attachments,
                )
                response = self._client.send_raw_email(
                    Source=self._sender,
                    Destinations=[recipient],
                    RawMessage={"Data": raw},
                )
            else:
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
    def _build_raw_message(
        sender: str,
        recipient: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: list[tuple[str, bytes, str]],
    ) -> str:
        """Build a multipart/mixed MIME message with attachments for SES.

        Structure: mixed( alternative(text, html), attachment* ) — the
        standard shape so clients render the body and list the files.
        """
        from email.mime.application import MIMEApplication
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(body_text, "plain", "utf-8"))
        alternative.attach(MIMEText(body_html, "html", "utf-8"))
        msg.attach(alternative)

        for filename, content, mime_type in attachments:
            _, _, subtype = mime_type.partition("/")
            part = MIMEApplication(content, _subtype=subtype or "octet-stream")
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)

        return msg.as_string()

    @staticmethod
    def _build_password_reset_html(reset_link: str) -> str:
        """Build the HTML template for password-reset emails."""
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
                    Reset your password
                </h2>
                <p style="color:#6B7280;margin-bottom:24px;font-size:14px;">
                    We received a request to reset your {settings.APP_NAME}
                    password. Click the button below to choose a new one.
                </p>
                <div style="text-align:center;margin:24px 0;">
                    <a href="{reset_link}"
                       style="display:inline-block;padding:12px 28px;background:#3a1078;
                              color:#fff;text-decoration:none;border-radius:8px;
                              font-size:15px;font-weight:600;">
                        Reset password
                    </a>
                </div>
                <p style="color:#6B7280;font-size:13px;margin-top:24px;">
                    This link expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES}
                    minutes. If you didn't request this, you can safely ignore
                    this email &mdash; your password will not change.
                </p>
                <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0;">
                <p style="color:#9CA3AF;font-size:12px;text-align:center;">
                    {settings.APP_NAME} &copy; {datetime.now().year}
                </p>
            </div>
        </body>
        </html>
        """

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
