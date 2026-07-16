"""Transactional email outbox.

Document emails are enqueued in the SAME database transaction as the
business-state change they announce (e.g. invoice DRAFT -> SENT), so a
delivery failure can never silently lose the email: the queued row is the
durable record, retried by the drainer until delivered or dead-lettered.

Delivery surface:
- ``EmailOutboxService.deliver_now`` — best-effort immediate delivery of one
  row, used by the send endpoints right after the locked phase commits, so
  the happy-path UX stays synchronous.
- ``EmailOutboxService.drain`` — batch retry of pending/failed rows, exposed
  as ``POST /internal/email-outbox/drain`` (X-Internal-Secret).

Scheduling: one external scheduler (GitLab scheduled pipeline /
k8s CronJob / cron + curl) should periodically call the internal job
endpoints: ``/invoices/internal/transition-overdue``,
``/quotes/internal/transition-expired``, ``/auth/internal/purge-otps`` and
``/internal/email-outbox/drain``. Alert if a job has not run in its window.

PDF attachments are rendered at delivery time from the document
reference (``document_type`` + ``document_id``) using the existing
per-module PDF renderers, so the locked send phase does no PDF work and
every retry attaches fresh bytes.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.database import Base

logger = logging.getLogger(__name__)

#: Delivery attempts before a message is dead-lettered.
MAX_DELIVERY_ATTEMPTS = 5


class EmailOutboxStatus:
    """Lifecycle of an outbox row."""

    PENDING = "pending"  # enqueued, not yet attempted
    SENT = "sent"  # delivered
    FAILED = "failed"  # transient failure; drainer will retry
    DEAD = "dead"  # attempts exhausted; needs operator attention


class EmailOutbox(Base):
    """One queued outbound email, written in-transaction with its trigger."""

    __tablename__ = "email_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)

    document_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment=(
            "Source document noun ('invoice', 'quote', 'purchase_order') "
            "for PDF rendering"
        ),
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    attach_pdf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=EmailOutboxStatus.PENDING,
        server_default=text("'pending'"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<EmailOutbox(id={self.id}, status={self.status}, "
            f"attempts={self.attempts}, recipient={self.recipient})>"
        )


class EmailOutboxService:
    """Enqueue and deliver outbox rows.

    Delivery commits its own bookkeeping (attempts / last_error / status)
    so a failed attempt is durable even when the surrounding request later
    rolls back — the queue is the source of truth for delivery state.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def enqueue(
        self,
        *,
        recipient: str,
        subject: str,
        body_text: str,
        document_type: str | None = None,
        document_id: uuid.UUID | None = None,
        attach_pdf: bool = False,
    ) -> EmailOutbox:
        """Queue an email. Flushes only — the caller owns the commit, so the
        row becomes durable atomically with the caller's state change."""
        row = EmailOutbox(
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            document_type=document_type,
            document_id=document_id,
            attach_pdf=attach_pdf,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def deliver_now(self, outbox_id: uuid.UUID) -> bool:
        """Best-effort immediate delivery of one queued row.

        Returns True when the row is (already) delivered. Failures are
        recorded on the row and NOT propagated: durability comes from the
        queue, the caller only needs to know whether to say 'sent' or
        'queued for retry'.
        """
        row = self._db.get(EmailOutbox, outbox_id)
        if row is None:
            logger.error("Outbox row %s not found for immediate delivery", outbox_id)
            return False
        if row.status == EmailOutboxStatus.SENT:
            return True
        return self._attempt(row)

    def drain(self, limit: int = 50) -> dict[str, int]:
        """Retry queued/failed rows, oldest first (scheduler entry point).

        ``SKIP LOCKED`` lets multiple drainers run concurrently without
        double-sending (no-op on the SQLite test fallback).
        """
        rows = (
            self._db.query(EmailOutbox)
            .filter(
                EmailOutbox.status.in_(
                    [EmailOutboxStatus.PENDING, EmailOutboxStatus.FAILED]
                )
            )
            .order_by(EmailOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .all()
        )

        delivered = 0
        failed = 0
        dead = 0
        for row in rows:
            if self._attempt(row):
                delivered += 1
            elif row.status == EmailOutboxStatus.DEAD:
                dead += 1
            else:
                failed += 1

        logger.info(
            "Email outbox drained",
            extra={
                "processed": len(rows),
                "delivered": delivered,
                "failed": failed,
                "dead": dead,
            },
        )
        return {
            "processed": len(rows),
            "delivered": delivered,
            "failed": failed,
            "dead": dead,
        }

    # Dead-letter queue (operator tooling)

    def list_dead(self, limit: int = 50) -> list[dict]:
        """Inspect dead-lettered rows (operator entry point).

        Returns the oldest ``limit`` rows whose delivery attempts are
        exhausted, with enough context (recipient, subject, attempts,
        last_error) to diagnose the failure without touching the database.
        """
        rows = (
            self._db.query(EmailOutbox)
            .filter(EmailOutbox.status == EmailOutboxStatus.DEAD)
            .order_by(EmailOutbox.created_at)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(row.id),
                "recipient": row.recipient,
                "subject": row.subject,
                "document_type": row.document_type,
                "document_id": str(row.document_id) if row.document_id else None,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    def requeue_dead(
        self,
        outbox_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> dict[str, int]:
        """Return dead-lettered rows to the retry queue (operator action).

        Resets status to FAILED with a fresh attempt budget so the drainer
        picks the rows up again — use only after the delivery root cause
        has been fixed. Targets one row when ``outbox_id`` is given, else
        the oldest ``limit`` dead rows. ``SKIP LOCKED`` keeps a concurrent
        drainer from racing the requeue.
        """
        query = self._db.query(EmailOutbox).filter(
            EmailOutbox.status == EmailOutboxStatus.DEAD
        )
        if outbox_id is not None:
            query = query.filter(EmailOutbox.id == outbox_id)
        rows = (
            query.order_by(EmailOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .all()
        )
        for row in rows:
            row.status = EmailOutboxStatus.FAILED
            row.attempts = 0
            row.last_error = None
        self._db.commit()
        logger.info(
            "Email outbox dead rows requeued",
            extra={"requeued": len(rows)},
        )
        return {"requeued": len(rows)}

    # Internals

    def _attempt(self, row: EmailOutbox) -> bool:
        """One delivery attempt with durable bookkeeping."""
        from app.lib.email import email_service

        try:
            attachments = self._build_attachments(row)
            email_service.send_document_email(
                recipient=row.recipient,
                subject=row.subject,
                body_text=row.body_text,
                attachments=attachments,
            )
        except Exception as exc:
            row.attempts += 1
            row.last_error = str(exc)[:2000]
            row.status = (
                EmailOutboxStatus.DEAD
                if row.attempts >= MAX_DELIVERY_ATTEMPTS
                else EmailOutboxStatus.FAILED
            )
            self._db.commit()
            logger.error(
                "Email outbox delivery failed",
                exc_info=exc,
                extra={
                    "outbox_id": str(row.id),
                    "attempts": row.attempts,
                    "status": row.status,
                },
            )
            return False

        row.attempts += 1
        row.status = EmailOutboxStatus.SENT
        row.sent_at = datetime.now(UTC)
        row.last_error = None
        self._db.commit()
        return True

    def _build_attachments(
        self, row: EmailOutbox
    ) -> list[tuple[str, bytes, str]] | None:
        """Render the document PDF at delivery time."""
        if not row.attach_pdf or row.document_type is None or row.document_id is None:
            return None

        if row.document_type == "invoice":
            from app.modules.invoices.service import InvoiceService

            pdf_bytes, invoice = InvoiceService(self._db).generate_pdf_for_download(
                row.document_id
            )
            return [
                (
                    f"Invoice_{invoice.invoice_reference}.pdf",
                    pdf_bytes,
                    "application/pdf",
                )
            ]

        if row.document_type == "quote":
            from app.modules.quotes.service import QuoteService

            service = QuoteService(self._db)
            quote = service.get_by_id(row.document_id)
            return [
                (
                    f"Quote_{quote.quote_reference}.pdf",
                    service.generate_pdf(row.document_id),
                    "application/pdf",
                )
            ]

        if row.document_type == "purchase_order":
            from app.modules.purchase_orders.service import PurchaseOrderService

            pdf_bytes, purchase_order = PurchaseOrderService(
                self._db
            ).generate_pdf_for_download(row.document_id)
            return [
                (
                    f"PurchaseOrder_{purchase_order.po_reference}.pdf",
                    pdf_bytes,
                    "application/pdf",
                )
            ]

        logger.warning(
            "Unknown outbox document_type %r; sending without attachment",
            row.document_type,
        )
        return None
