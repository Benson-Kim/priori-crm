"""Shared base class and mixins for document services (invoices, quotes, expenses).

Extracts the logic that was duplicated near-verbatim across
``InvoiceService``, ``QuoteService`` and ``ExpenseService``:

- ``StateMachineMixin``    - the ``_transition()`` guard + atomic version bump.
- ``ReferenceRetryMixin``  - the lazy ``ReferenceGenerator`` accessor and the
                             unique-reference collision predicate used by the
                             ``begin_nested()`` create/duplicate retry loops.
- ``DocumentEmailMixin``   - the customer-facing email subject/body builder.
- ``BaseDocumentService``  - the common ``__init__`` (db + current_user/actor)
                             composed with the mixins above.

This is a behaviour-preserving refactor. The transition *tables*
(``ALLOWED_TRANSITIONS``) and reference *formats* stay defined on each concrete
service; only the mechanics move here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, lazyload

from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.common.reference import ReferenceGenerator

logger = logging.getLogger(__name__)


class StateMachineMixin:
    """Enforce a per-service status state machine with an atomic version bump.

    Concrete services define ``ALLOWED_TRANSITIONS`` (a mapping of current
    status -> list of permitted next statuses) and ``_document_noun`` (used in
    the error message, e.g. ``"invoice"``). The transition mechanics - the
    guard, the status assignment and the ``version`` increment - are shared.
    """

    # Overridden on each concrete service.
    ALLOWED_TRANSITIONS: ClassVar[dict[Any, list[Any]]] = {}
    _document_noun: ClassVar[str] = "document"

    def _transition(self, entity: Any, new_status: Any) -> None:
        """Enforce ``ALLOWED_TRANSITIONS`` and bump ``version`` atomically.

        Raises ``BadRequestException`` for any disallowed transition so callers
        never need to know the rules themselves.
        """
        current = type(new_status)(entity.status)
        allowed = self.ALLOWED_TRANSITIONS.get(current, [])
        if new_status not in allowed:
            raise BadRequestException(
                detail=(
                    f"Cannot transition {self._document_noun} from '{current}' "
                    f"to '{new_status}'. "
                    f"Allowed: {[s.value for s in allowed] or 'none (terminal state)'}"
                ),
                field="status",
            )
        entity.status = new_status
        entity.version += 1


class ReferenceRetryMixin:
    """Shared reference-generation accessor + collision detection.

    The concrete create/duplicate methods keep their own ``begin_nested()``
    retry loops (the surrounding entity construction differs per model), but
    the parts that were identical - obtaining a :class:`ReferenceGenerator`
    and deciding whether an ``IntegrityError`` is a reference collision - live
    here.
    """

    #: Substrings that identify a unique-reference collision in an
    #: ``IntegrityError``. Overridden per service (e.g. invoice_number).
    _reference_collision_markers: ClassVar[tuple[str, ...]] = ()

    _db: Session

    def _ref_gen(self) -> ReferenceGenerator:
        """Lazy accessor for the shared reference generator."""
        return ReferenceGenerator(self._db)

    #: Bounded retries for unique-reference collisions (the advisory lock
    #: makes collisions very rare; the DB unique constraint is the net).
    MAX_REFERENCE_RETRIES: ClassVar[int] = 3

    def _is_reference_collision(self, error: Exception) -> bool:
        """True if ``error`` is a unique-constraint clash on a reference column."""
        orig = str(getattr(error, "orig", error))
        return any(marker in orig for marker in self._reference_collision_markers)

    def _with_reference_retry(self, build_fn: Callable[[], Any], noun: str) -> Any:
        """SAVEPOINT-based create/duplicate retry template.

        Runs ``build_fn`` inside a SAVEPOINT. On a unique-reference
        IntegrityError only the savepoint rolls back (never the whole
        session) and the build retries with freshly generated references,
        up to ``MAX_REFERENCE_RETRIES`` times. Any other integrity error
        surfaces immediately as a 409. Replaces the six near-identical
        loops across the invoice/quote/expense create and duplicate paths.
        """
        last_error: Exception | None = None
        for attempt in range(self.MAX_REFERENCE_RETRIES + 1):
            sp = self._db.begin_nested()
            try:
                result = build_fn()
                sp.commit()
                return result
            except IntegrityError as exc:
                sp.rollback()
                last_error = exc
                if (
                    self._is_reference_collision(exc)
                    and attempt < self.MAX_REFERENCE_RETRIES
                ):
                    logger.warning(
                        "%s reference collision, retry %d", noun, attempt + 1
                    )
                    continue
                raise ConflictException(
                    f"{noun.capitalize()} data violates database constraints"
                ) from exc

        raise ConflictException(
            f"Failed to generate unique {noun} number after retries"
        ) from last_error


class DocumentEmailMixin:
    """Build the customer-facing email subject/body for a document.

    Invoices and quotes share an identical template that differs only in the
    document noun and the date label ("Due date" vs "Valid until"); both come
    from the service-level ``_email_terms`` mapping.
    """

    #: Per-service wording. Keys: ``noun`` ("invoice"), ``date_label``
    #: ("Due date"), ``closing`` (the sign-off sentence).
    _email_terms: ClassVar[dict[str, str]] = {
        "noun": "document",
        "date_label": "Due date",
        "closing": "Thank you for your business.",
    }

    def _document_email_reference(self, entity: Any) -> str:
        """Return the human reference printed in the email (overridable)."""
        # Invoices/quotes expose ``<noun>_reference``; fall back generically.
        return getattr(
            entity,
            f"{self._email_terms['noun']}_reference",
            getattr(entity, "reference", ""),
        )

    def _generate_email_subject(self, entity: Any) -> str:
        """Generate the email subject line for a document."""
        from app.lib.config import settings

        noun = self._email_terms["noun"].capitalize()
        return (
            f"{noun} {self._document_email_reference(entity)} from {settings.APP_NAME}"
        )

    def _generate_email_body(self, entity: Any, attached: bool = False) -> str:
        """Generate the plain-text email body for a document.

        ``attached`` switches the intro line: the email only claims a PDF
        is attached when delivery will actually attach one.
        """
        from app.lib.config import settings

        terms = self._email_terms
        reference = self._document_email_reference(entity)
        if attached:
            intro = (
                f"Please find attached {terms['noun']} {reference} "
                f"for {entity.currency} {entity.total_due}."
            )
        else:
            intro = (
                f"Please find the details of {terms['noun']} {reference} "
                f"for {entity.currency} {entity.total_due} below."
            )
        return f"""\
Dear {entity.customer.display_name},

{intro}

{terms["date_label"]}: {entity.due_date.strftime("%d %B %Y")}

{terms["closing"]}

Best regards,
{settings.APP_NAME}
"""


class DocumentSendMixin:
    """Two-phase email send shared by invoices and quotes.

    Phase 1 — ``_prepare_and_mark_sent`` (locked): re-read the bare row
    ``FOR UPDATE`` (``lazyload('*')`` keeps the lock off the nullable side
    of the customer outer join, which PostgreSQL rejects), run the
    per-service ``_validate_sendable`` guard, transition DRAFT -> SENT,
    capture the owner snapshot, flush and COMMIT. The commit releases the
    row lock and makes the status change durable *before* any network I/O.

    Phase 2 — the caller dispatches the email with no lock held, so a slow
    or failing SES call can never hold a row lock or serialize concurrent
    operations on the document.

    Atomicity-contract note: the mid-request commit here is a
    deliberate, documented exception to the get_db()-owns-the-commit rule;
    a later failure in the same request cannot roll the SENT transition
    back.

    Concrete services declare ``_send_model``, ``_send_draft_status`` and
    ``_send_sent_status``, and may override ``_validate_sendable``.
    """

    _send_model: ClassVar[Any] = None
    _send_draft_status: ClassVar[Any] = None
    _send_sent_status: ClassVar[Any] = None

    def _validate_sendable(self, entity: Any) -> None:
        """Per-service guard for unsendable statuses (override)."""

    def _resolve_recipient(self, entity: Any, to_email: str | None) -> str:
        """Resolve the send recipient (override per document type).

        Default: an explicit ``to_email`` wins, else the customer's email.
        Vendor-facing documents (purchase orders) override this to resolve
        the vendor's email instead. Raises ``BadRequestException`` when no
        address is available so Send is blocked server-side (defence in
        depth alongside the disabled UI button).
        """
        recipient = to_email or entity.customer.email
        if not recipient:
            raise BadRequestException(
                detail="No email address available for this customer",
                field="to_email",
            )
        return recipient

    def _get_locked(self, entity_id: Any) -> Any:
        """Re-read the bare row ``FOR UPDATE`` or raise 404.

        ``lazyload('*')`` keeps the lock off the nullable side of the
        customer outer join (PostgreSQL rejects FOR UPDATE there). All
        status transitions should load through this helper so concurrent
        transitions serialize on the row lock instead of racing.
        """
        model = self._send_model
        entity = (
            self._db.query(model)
            .options(lazyload("*"))
            .filter(model.id == entity_id)
            .with_for_update()
            .first()
        )
        if not entity:
            raise NotFoundException(
                detail=f"{self._document_noun.capitalize()} '{entity_id}' not found",
                resource=self._document_noun,
            )
        return entity

    def _prepare_and_mark_sent(
        self,
        entity_id: Any,
        to_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        attach_pdf: bool = False,
    ) -> tuple[str, str, str, datetime, Any]:
        """Locked phase of send: validate, transition, enqueue, then commit.

        Returns the resolved recipient, subject, body, sent_at timestamp and
        the id of the outbox row enqueued for delivery. The outbox row
        commits atomically with the status change, so a SENT
        document always has a durable delivery record; the commit also
        releases the row lock ahead of any dispatch in the caller.
        """
        entity = self._get_locked(entity_id)

        self._validate_sendable(entity)

        recipient = self._resolve_recipient(entity, to_email)

        email_subject = subject or self._generate_email_subject(entity)
        email_body = body or self._generate_email_body(entity, attached=attach_pdf)

        # Mark as sent if currently draft.
        sent_at = datetime.now(UTC)
        if entity.status == self._send_draft_status:
            self._transition(entity, self._send_sent_status)
            entity.sent_at = sent_at
            self._capture_owner_snapshot(entity)
            self._db.flush()

        # Transactional outbox: queued in the SAME transaction
        # as the status change above.
        from app.common.email_outbox import EmailOutboxService

        outbox_row = EmailOutboxService(self._db).enqueue(
            recipient=recipient,
            subject=email_subject,
            body_text=email_body,
            document_type=self._document_noun,
            document_id=entity.id,
            attach_pdf=attach_pdf,
        )
        outbox_id = outbox_row.id

        self._db.commit()

        return recipient, email_subject, email_body, sent_at, outbox_id


class ServiceBase:
    """Shared constructor + public actor surface for ALL services.

    Document and non-document services (customers, vendors) carried
    identical hand-rolled ``__init__`` bodies; this is now the single home
    for the session handle and the authenticated-actor accessors.
    """

    def __init__(self, db: Session, current_user: Any = None) -> None:
        self._db = db
        self._current_user = current_user
        self._actor_id = getattr(current_user, "id", None)

    @property
    def actor_id(self) -> Any | None:
        """Id of the authenticated user driving this service call.

        Public accessor so routers never reach into service
        privates.
        """
        return self._actor_id

    def require_privileged_actor(self, action: str) -> None:
        """Raise 403 unless the current actor holds a privileged role.

        Owns the role inspection so routers do not re-implement
        require_privileged against service internals.
        """
        from app.common.exceptions import ForbiddenException
        from app.constants.enums import UserRole

        role = getattr(self._current_user, "role", None)
        if role is None or not UserRole(role).is_privileged:
            raise ForbiddenException(
                detail=f"You do not have permission to {action}.",
                required_permission="admin/manager",
            )


class BaseDocumentService(
    StateMachineMixin,
    ReferenceRetryMixin,
    DocumentEmailMixin,
    DocumentSendMixin,
    ServiceBase,
):
    """Common base for the document services.

    Composes the state-machine, reference-retry, email and two-phase-send
    mixins on top of the shared ServiceBase constructor/actor surface.
    Concrete services add their model-specific CRUD, calculation and
    statistics logic.
    """

    def _load_owner_branding(self, entity: Any) -> tuple[Any, Any]:
        """Resolve the owner header (info + logo bytes) for a document PDF.

        Delegates to the shared DocumentPdfRenderer; kept as a
        method for backward compatibility with existing callers.
        """
        from app.common.pdf_renderer import DocumentPdfRenderer

        return DocumentPdfRenderer(self._db).load_owner_branding(entity)

    def _resolve_vat(
        self,
        vat_enabled: bool | None,
        vat_rate: Any,
        *,
        fields_sent: set[str],
    ) -> tuple[bool, Any]:
        """Resolve the effective document-level VAT settings for create.

        When the client sent neither VAT field, the document defaults to
        the owner profile's VAT settings (our own VAT), so new documents
        inherit the org configuration. An explicitly supplied toggle always
        wins. Raises 400 when VAT ends up enabled without a rate.
        """
        from decimal import Decimal

        if fields_sent:
            enabled = bool(vat_enabled)
            rate = vat_rate if enabled else None
        else:
            from app.modules.owner.service import OwnerService

            profile = OwnerService(self._db).get_or_create()
            enabled = bool(profile.vat_enabled)
            rate = (
                Decimal(str(profile.vat_rate))
                if enabled and profile.vat_rate is not None
                else None
            )

        if enabled and rate is None:
            raise BadRequestException(
                detail="vat_rate is required when vat_enabled is true",
                field="vat_rate",
            )
        return enabled, rate

    def _resolve_vat_compliance_ref(
        self,
        vat_compliance_ref: str | None,
        *,
        field_sent: bool,
        vat_enabled: bool,
    ) -> str | None:
        """Resolve the document's VAT compliance reference for create.

        Mirrors PO-27 Option A: an explicitly supplied reference always
        wins (blank normalises to NULL); when omitted and VAT is enabled,
        it defaults from the owner profile's tax PIN so issued documents
        carry our own registration number.
        """
        if field_sent:
            cleaned = (vat_compliance_ref or "").strip()
            return cleaned or None
        if not vat_enabled:
            return None

        from app.modules.owner.service import OwnerService

        pin = (OwnerService(self._db).get_or_create().tax_pin or "").strip()
        return pin or None

    #: Response class returned by calculate_totals (set per service).
    _calculation_response_cls: ClassVar[Any] = None

    @classmethod
    def calculate_totals(
        cls,
        line_items: list[Any],
        discount_type: Any = None,
        discount_amount: Any = None,
        discount_percentage: Any = None,
        vat_enabled: bool = False,
        vat_rate: Any = None,
    ) -> Any:
        """Calculate document totals without saving (preview).

        Shared by invoices and quotes: the computation was
        duplicated verbatim, differing only in the response class, which
        concrete services declare via ``_calculation_response_cls``.

        ``vat_enabled`` / ``vat_rate`` switch on document-level VAT:
        per-line tax is neutralised and VAT is charged once on the
        discounted subtotal, mirroring the persisted computation.
        """
        from app.common.financial import (
            build_line_items,
            calculate_discount,
            calculate_subtotal_vat,
            neutralize_line_tax,
            sum_line_totals,
        )

        built_items = build_line_items(line_items)
        if vat_enabled:
            neutralize_line_tax(built_items)
        subtotal, tax_total = sum_line_totals(built_items)
        calculated_items = [
            {
                "item_name": item["item_name"],
                "description": item["description"],
                "quantity": float(item["quantity"]),
                "unit_price": float(item["unit_price"]),
                "line_total": float(item["line_total"]),
                "tax_type": item["tax_type"],
                "tax_amount": float(item["tax_amount"]),
            }
            for item in built_items
        ]

        discount_value = calculate_discount(
            subtotal, discount_type, discount_amount, discount_percentage
        )
        if vat_enabled:
            # Document-level VAT is charged once on the discounted base.
            tax_total = calculate_subtotal_vat(
                subtotal - discount_value, vat_enabled, vat_rate
            )
        total_due = subtotal - discount_value + tax_total

        return cls._calculation_response_cls(
            subtotal=subtotal,
            discount_value=discount_value,
            tax_total=tax_total,
            total_due=total_due,
            line_items=calculated_items,
        )
