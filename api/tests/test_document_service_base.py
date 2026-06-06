"""W3.4 — BaseDocumentService + mixins (P-7, V-SOLID-1, V-DRY-2).

Pure unit tests for the extracted shared mechanics. No database, no Postgres:
the mixins are exercised with lightweight stand-in objects so the refactor's
contract is locked independently of the concrete services (whose end-to-end
behaviour is already covered by test_status_state_machine.py et al.).
"""
import enum
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.common.document_service import (
    BaseDocumentService,
    DocumentEmailMixin,
    ReferenceRetryMixin,
    StateMachineMixin,
)
from app.common.exceptions import BadRequestException


class _Status(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    DONE = "done"


# ── StateMachineMixin ─────────────────────────────────────────────


class _SM(StateMachineMixin):
    _document_noun = "widget"
    ALLOWED_TRANSITIONS = {
        _Status.DRAFT: [_Status.SENT],
        _Status.SENT: [_Status.DONE],
        _Status.DONE: [],
    }


class TestStateMachineMixin:
    def test_allowed_transition_sets_status_and_bumps_version(self):
        entity = SimpleNamespace(status=_Status.DRAFT, version=1)
        _SM()._transition(entity, _Status.SENT)
        assert entity.status == _Status.SENT
        assert entity.version == 2

    def test_disallowed_transition_raises_and_leaves_entity_untouched(self):
        entity = SimpleNamespace(status=_Status.DRAFT, version=1)
        with pytest.raises(BadRequestException):
            _SM()._transition(entity, _Status.DONE)
        assert entity.status == _Status.DRAFT
        assert entity.version == 1

    def test_terminal_state_allows_no_transition(self):
        entity = SimpleNamespace(status=_Status.DONE, version=5)
        with pytest.raises(BadRequestException):
            _SM()._transition(entity, _Status.SENT)

    def test_error_message_uses_document_noun(self):
        entity = SimpleNamespace(status=_Status.DRAFT, version=1)
        with pytest.raises(BadRequestException) as exc:
            _SM()._transition(entity, _Status.DONE)
        assert "widget" in str(exc.value.detail)


# ── ReferenceRetryMixin ─────────────────────────────────────────


class _RR(ReferenceRetryMixin):
    _reference_collision_markers = ("widget_number", "widget_reference")


class TestReferenceRetryMixin:
    def test_collision_detected_on_marker_in_orig(self):
        err = Exception()
        err.orig = "duplicate key value violates unique constraint widget_number_key"
        assert _RR()._is_reference_collision(err) is True

    def test_non_collision_integrity_error_not_misclassified(self):
        err = Exception()
        err.orig = "null value in column customer_id violates not-null constraint"
        assert _RR()._is_reference_collision(err) is False

    def test_falls_back_to_str_when_no_orig(self):
        assert _RR()._is_reference_collision(Exception("widget_reference clash")) is True
        assert _RR()._is_reference_collision(Exception("something else")) is False


# ── DocumentEmailMixin ──────────────────────────────────────────


class _InvoiceEmail(DocumentEmailMixin):
    _email_terms = {
        "noun": "invoice",
        "date_label": "Due date",
        "closing": "Thank you for your business.",
    }


class _QuoteEmail(DocumentEmailMixin):
    _email_terms = {
        "noun": "quote",
        "date_label": "Valid until",
        "closing": "Thank you for considering our proposal.",
    }


def _doc(reference_attr, reference_value):
    from datetime import date

    return SimpleNamespace(
        customer=SimpleNamespace(display_name="Acme Ltd"),
        currency="KES",
        total_due=Decimal("1500.00"),
        due_date=date(2026, 6, 30),
        **{reference_attr: reference_value},
    )


class TestDocumentEmailMixin:
    def test_invoice_subject_and_body_wording(self):
        doc = _doc("invoice_reference", "IN-0042")
        svc = _InvoiceEmail()
        assert "Invoice IN-0042" in svc._generate_email_subject(doc)
        body = svc._generate_email_body(doc)
        assert "invoice IN-0042" in body
        assert "Due date: 30 June 2026" in body
        assert "Thank you for your business." in body

    def test_quote_subject_and_body_wording(self):
        doc = _doc("quote_reference", "QT-0007")
        svc = _QuoteEmail()
        assert "Quote QT-0007" in svc._generate_email_subject(doc)
        body = svc._generate_email_body(doc)
        assert "quote QT-0007" in body
        assert "Valid until: 30 June 2026" in body
        assert "Thank you for considering our proposal." in body


# ── Concrete services are wired onto the base ───────────────────────────


class TestConcreteServicesUseBase:
    def test_all_three_services_subclass_base(self):
        from app.modules.expenses.service import ExpenseService
        from app.modules.invoices.service import InvoiceService
        from app.modules.quotes.service import QuoteService

        assert issubclass(InvoiceService, BaseDocumentService)
        assert issubclass(QuoteService, BaseDocumentService)
        assert issubclass(ExpenseService, BaseDocumentService)

    def test_services_keep_their_own_transition_tables(self):
        from app.constants.enums import ExpenseStatus, InvoiceStatus, QuoteStatus
        from app.modules.expenses.service import ExpenseService
        from app.modules.invoices.service import InvoiceService
        from app.modules.quotes.service import QuoteService

        assert InvoiceStatus.DRAFT in InvoiceService.ALLOWED_TRANSITIONS
        assert QuoteStatus.DRAFT in QuoteService.ALLOWED_TRANSITIONS
        assert ExpenseStatus.PENDING in ExpenseService.ALLOWED_TRANSITIONS

    def test_services_define_their_collision_markers(self):
        from app.modules.expenses.service import ExpenseService
        from app.modules.invoices.service import InvoiceService
        from app.modules.quotes.service import QuoteService

        assert "invoice_number" in InvoiceService._reference_collision_markers
        assert "quote_number" in QuoteService._reference_collision_markers
        assert "expense_number" in ExpenseService._reference_collision_markers
