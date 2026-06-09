"""Shared base class and mixins for document services (invoices, quotes, expenses).

Extracts the logic that was duplicated near-verbatim across
``InvoiceService``, ``QuoteService`` and ``ExpenseService`` (review findings
P-7 / V-SOLID-1 / V-DRY-2):

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

from typing import Any, ClassVar

from sqlalchemy.orm import Session

from app.common.exceptions import BadRequestException
from app.common.reference import ReferenceGenerator


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

    def _is_reference_collision(self, error: Exception) -> bool:
        """True if ``error`` is a unique-constraint clash on a reference column."""
        orig = str(getattr(error, "orig", error))
        return any(marker in orig for marker in self._reference_collision_markers)


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

    def _generate_email_body(self, entity: Any) -> str:
        """Generate the plain-text email body for a document."""
        from app.lib.config import settings

        terms = self._email_terms
        reference = self._document_email_reference(entity)
        return f"""\
Dear {entity.customer.display_name},

Please find attached {terms["noun"]} {reference} for {entity.currency} {entity.total_due}.

{terms["date_label"]}: {entity.due_date.strftime("%d %B %Y")}

{terms["closing"]}

Best regards,
{settings.APP_NAME}
"""


class BaseDocumentService(
    StateMachineMixin,
    ReferenceRetryMixin,
    DocumentEmailMixin,
):
    """Common base for the document services.

    Carries the shared constructor (session + optional ``current_user`` and the
    derived ``_actor_id``) and composes the state-machine, reference-retry and
    email mixins. Concrete services add their model-specific CRUD, calculation
    and statistics logic.
    """

    def __init__(self, db: Session, current_user: Any = None) -> None:
        self._db = db
        self._current_user = current_user
        self._actor_id = getattr(current_user, "id", None)
