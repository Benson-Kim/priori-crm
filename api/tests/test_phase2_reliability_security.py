"""Phase 2 reliability/security regression tests (docs/architecture-review.md).

Covers transactional email outbox, PDF attachments,
refresh-token family revocation + hashed OTP codes.
"""

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from email import message_from_string

import pytest

from app.common.email_outbox import (
    MAX_DELIVERY_ATTEMPTS,
    EmailOutbox,
    EmailOutboxService,
    EmailOutboxStatus,
)
from app.common.exceptions import EmailDeliveryException, UnauthorizedException
from app.common.security import create_refresh_token, hash_password
from app.constants.enums import Currency, CustomerStatus, QuoteStatus
from app.lib.email import EmailService
from app.modules.auth.models import OTPCode, User
from app.modules.auth.service import AuthService
from app.modules.customers.models import Customer
from app.modules.quotes.models import Quote
from app.modules.quotes.service import QuoteService


class StubEmailService:
    """Records send_document_email calls; optionally fails."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    def send_document_email(self, recipient, subject, body_text, attachments=None):
        if self.fail:
            raise EmailDeliveryException(detail="SES unavailable", recipient=recipient)
        self.calls.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body_text": body_text,
                "attachments": attachments,
            }
        )
        return {"MessageId": "stub"}


@pytest.fixture
def stub_email(monkeypatch):
    stub = StubEmailService()
    monkeypatch.setattr("app.lib.email.email_service", stub)
    return stub


@pytest.fixture
def no_owner_snapshot(monkeypatch):
    """Skip the owner-snapshot capture so tests need no owner fixtures."""
    monkeypatch.setattr(
        QuoteService, "_capture_owner_snapshot", lambda self, entity: None
    )


def _customer(db, email="outbox@acme.test") -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme",
        first_name="Ada",
        last_name="L",
        email=email,
        phone="+254700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _draft_quote(db, customer, number="QTE-P2-1") -> Quote:
    today = date.today()
    q = Quote(
        quote_number=number,
        quote_reference=number.replace("QTE", "QT"),
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=QuoteStatus.DRAFT,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        total_due=Decimal("116.00"),
        version=1,
    )
    db.add(q)
    db.flush()
    return q


class TestEmailOutbox:
    def test_send_quote_enqueues_and_delivers(self, db, stub_email, no_owner_snapshot):
        customer = _customer(db)
        quote = _draft_quote(db, customer)
        service = QuoteService(db)

        result = service.send_quote(quote.id, attach_pdf=False)

        assert result["message"] == "Quote sent successfully"
        assert result["sent_to"] == customer.email
        assert len(stub_email.calls) == 1
        # Without attach_pdf the body must not claim an attachment.
        assert "attached" not in stub_email.calls[0]["body_text"]
        assert "below" in stub_email.calls[0]["body_text"]

        row = db.query(EmailOutbox).one()
        assert row.status == EmailOutboxStatus.SENT
        assert row.attempts == 1
        assert row.document_type == "quote"
        assert row.document_id == quote.id
        assert row.sent_at is not None

    def test_failed_delivery_stays_queued(self, db, stub_email, no_owner_snapshot):
        stub_email.fail = True
        customer = _customer(db, email="fail@acme.test")
        quote = _draft_quote(db, customer, number="QTE-P2-FAIL")
        service = QuoteService(db)

        result = service.send_quote(quote.id, attach_pdf=False)

        # The status change is durable and the failure is recorded, not lost.
        assert "queued for delivery" in result["message"]
        db.expire_all()
        assert (
            db.query(Quote).filter(Quote.id == quote.id).one().status
            == QuoteStatus.SENT
        )
        row = db.query(EmailOutbox).one()
        assert row.status == EmailOutboxStatus.FAILED
        assert row.attempts == 1
        assert "SES unavailable" in row.last_error

        # Provider recovers: the drainer delivers the queued row.
        stub_email.fail = False
        stats = EmailOutboxService(db).drain()
        assert stats == {"processed": 1, "delivered": 1, "failed": 0, "dead": 0}
        db.expire_all()
        row = db.query(EmailOutbox).one()
        assert row.status == EmailOutboxStatus.SENT
        assert row.attempts == 2
        assert row.last_error is None

    def test_dead_letter_after_max_attempts(self, db, stub_email):
        stub_email.fail = True
        row = EmailOutboxService(db).enqueue(
            recipient="dead@acme.test",
            subject="s",
            body_text="b",
        )
        row.attempts = MAX_DELIVERY_ATTEMPTS - 1
        row.status = EmailOutboxStatus.FAILED
        db.flush()

        stats = EmailOutboxService(db).drain()
        assert stats["dead"] == 1
        db.expire_all()
        row = db.query(EmailOutbox).one()
        assert row.status == EmailOutboxStatus.DEAD
        assert row.attempts == MAX_DELIVERY_ATTEMPTS

    def test_attach_pdf_renders_and_attaches(
        self, db, stub_email, no_owner_snapshot, monkeypatch
    ):
        monkeypatch.setattr(
            QuoteService, "generate_pdf", lambda self, quote_id: b"%PDF-1.4 stub"
        )
        customer = _customer(db, email="pdf@acme.test")
        quote = _draft_quote(db, customer, number="QTE-P2-PDF")
        service = QuoteService(db)

        result = service.send_quote(quote.id, attach_pdf=True)

        assert result["message"] == "Quote sent successfully"
        call = stub_email.calls[0]
        # The body claims an attachment only because one is really attached.
        assert "attached" in call["body_text"]
        (filename, content, mime_type) = call["attachments"][0]
        assert filename == f"Quote_{quote.quote_reference}.pdf"
        assert content == b"%PDF-1.4 stub"
        assert mime_type == "application/pdf"


class TestRefreshTokenReuse:
    def _user(self, db, email="reuse@acme.test") -> User:
        user = User(
            email=email,
            password_hash=hash_password("pw-123456"),
            first_name="Ada",
            last_name="L",
        )
        db.add(user)
        db.flush()
        return user

    def test_reuse_revokes_descendant_family(self, db):
        user = self._user(db)
        service = AuthService(db)

        token1, _jti, _exp = create_refresh_token(str(user.id))

        # Legitimate rotation: token1 is spent, token2 is live.
        _access, token2 = service.refresh_access_token(token1)

        # Theft scenario: the spent token is presented again.
        with pytest.raises(UnauthorizedException):
            service.refresh_access_token(token1)

        # Family fence: the descendant token2 (never individually revoked)
        # must now be rejected as well — the thief's chain dies with it.
        with pytest.raises(UnauthorizedException):
            service.refresh_access_token(token2)

    def test_normal_rotation_unaffected(self, db):
        user = self._user(db, email="normal@acme.test")
        service = AuthService(db)

        token1, _jti, _exp = create_refresh_token(str(user.id))
        _access, token2 = service.refresh_access_token(token1)
        # No reuse occurred: the rotated token keeps working.
        _access2, token3 = service.refresh_access_token(token2)
        assert token3 != token2


class TestOtpHashing:
    def _user(self, db, email="otp@acme.test") -> User:
        user = User(
            email=email,
            password_hash=hash_password("pw-123456"),
            first_name="Ada",
            last_name="L",
        )
        db.add(user)
        db.flush()
        return user

    def test_code_stored_hashed(self, db):
        user = self._user(db)
        service = AuthService(db)

        code = service._create_otp(user)
        row = db.query(OTPCode).filter(OTPCode.user_id == user.id).one()

        assert row.code != code
        assert row.code == hashlib.sha256(code.encode()).hexdigest()

    def test_verify_accepts_plaintext_and_rejects_wrong_code(self, db):
        user = self._user(db, email="otp-verify@acme.test")
        service = AuthService(db)
        code = service._create_otp(user)

        wrong = "000000" if code != "000000" else "111111"
        with pytest.raises(UnauthorizedException):
            service.verify_otp(user.email, wrong)

        access, refresh, verified_user = service.verify_otp(user.email, code)
        assert verified_user.id == user.id
        assert access and refresh


class TestMimeAttachments:
    @pytest.mark.no_db
    def test_build_raw_message_structure(self):
        raw = EmailService._build_raw_message(
            sender="noreply@priori.test",
            recipient="to@acme.test",
            subject="Invoice IN-0001",
            body_html="<p>hi</p>",
            body_text="hi",
            attachments=[("Invoice_IN-0001.pdf", b"%PDF-1.4", "application/pdf")],
        )

        msg = message_from_string(raw)
        assert msg.get_content_type() == "multipart/mixed"
        parts = list(msg.walk())
        content_types = [p.get_content_type() for p in parts]
        assert "multipart/alternative" in content_types
        assert "text/plain" in content_types
        assert "text/html" in content_types
        assert "application/pdf" in content_types

        pdf_part = next(p for p in parts if p.get_content_type() == "application/pdf")
        assert pdf_part.get_filename() == "Invoice_IN-0001.pdf"
        assert pdf_part.get_payload(decode=True) == b"%PDF-1.4"
