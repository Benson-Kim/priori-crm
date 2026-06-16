"""
Purchase Order document attachments (PO-12).

Covers upload/list/download/delete of per-PO attachments, mirroring the
Expenses Documents behaviour and reusing BaseDocumentService + the shared
object-storage / storage_tx helpers.

Acceptance criteria proven here:
- storage_key is NEVER returned in any API response (schema-level).
- attach persists storage_key + mime_type + file_size_bytes; file_size_kb
  is derived for the list view.
- attachments are addable at ANY status (DRAFT/SENT/BILLED/CANCELED) and do
  not re-open edit mode (PO version unchanged).
- get/delete are scoped to (po_id, document_id): a document on another PO
  is a 404, never cross-readable.
- delete returns the storage_key so the router can purge the object.
- a freshly written object is delete-on-rollback (no orphan on failed insert).
- hard delete purges the attachment blobs (no orphaned objects); the
  before-image audit row is still written.
- documents persist across PO updates; source is recorded (form/view).
- the shared upload guard rejects oversize/unsupported uploads with a 400.
- duplicate does NOT copy attachments.

The blob-purge-on-delete path uses the FOR UPDATE locked load, so the
full hard-delete test is Postgres-guarded; pure-logic and schema checks run
everywhere.
"""

import io
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.common.uploads import MAX_UPLOAD_SIZE_BYTES, validate_upload
from app.constants.enums import (
    Currency,
    DocumentSource,
    PurchaseOrderStatus,
    TaxType,
    VendorStatus,
)
from app.modules.purchase_orders.models import (
    PurchaseOrder,
    PurchaseOrderDocument,
)
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderDocumentResponse,
    PurchaseOrderLineItemCreate,
    PurchaseOrderUpdate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

# FIXTURES / HELPERS


class FakeStorage:
    """Records deletes; stands in for the storage facade in purge/rollback."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_file(self, storage_key: str) -> bool:
        self.deleted.append(storage_key)
        return True


def _vendor(db, *, name="Acme Supplies", currency=Currency.KES) -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        currency=currency,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line_item(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Steel bolts M8",
        "description": "Box of 500",
        "quantity": Decimal("2"),
        "unit_price": Decimal("100.00"),
        "tax_type": TaxType.VAT_16,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


def _create_payload(**kw) -> PurchaseOrderCreate:
    base = {"order_date": date.today(), "line_items": [_line_item()]}
    base.update(kw)
    return PurchaseOrderCreate(**base)


def _po(db, **kw) -> PurchaseOrder:
    vendor = kw.pop("vendor", None) or _vendor(db)
    svc = PurchaseOrderService(db)
    return svc.create(_create_payload(vendor_id=vendor.id, **kw))


def _attach(db, po, *, source=DocumentSource.FORM, key=None, storage=None):
    """Attach a document via the service, bypassing the HTTP storage write."""
    svc = PurchaseOrderService(db)
    return svc.attach_document(
        po_id=po.id,
        filename="receipt.pdf",
        file_size_bytes=2048,
        mime_type="application/pdf",
        storage_key=key or f"purchase_orders/{po.id}/{uuid.uuid4().hex}_receipt.pdf",
        source=source,
        storage=storage or FakeStorage(),
    )


# SCHEMA — storage_key must never leak


class TestSchemaSecurity:
    @pytest.mark.no_db
    def test_storage_key_absent_from_response_schema(self):
        assert "storage_key" not in PurchaseOrderDocumentResponse.model_fields

    @pytest.mark.no_db
    def test_response_carries_size_in_kb(self):
        # file_size_kb is part of the response contract for the list view.
        assert "file_size_kb" in PurchaseOrderDocumentResponse.model_fields


# ATTACH


class TestAttach:
    def test_attach_persists_metadata(self, db):
        po = _po(db)
        doc = _attach(db, po)

        assert doc.po_id == po.id
        assert doc.mime_type == "application/pdf"
        assert doc.file_size_bytes == 2048
        assert doc.storage_key  # stored internally
        # Derived KB used by the list view (2048 / 1024 = 2.0).
        assert doc.file_size_kb == 2.0
        assert doc.source == DocumentSource.FORM.value

    def test_response_model_omits_storage_key_value(self, db):
        po = _po(db)
        doc = _attach(db, po)
        dumped = PurchaseOrderDocumentResponse.model_validate(doc).model_dump()
        assert "storage_key" not in dumped

    def test_attach_unknown_po_raises_not_found(self, db):
        svc = PurchaseOrderService(db)
        with pytest.raises(NotFoundException):
            svc.attach_document(
                po_id=uuid.uuid4(),
                filename="x.pdf",
                file_size_bytes=10,
                mime_type="application/pdf",
                storage_key="purchase_orders/x/x.pdf",
                storage=FakeStorage(),
            )

    @pytest.mark.parametrize(
        "status",
        [
            PurchaseOrderStatus.DRAFT,
            PurchaseOrderStatus.SENT,
            PurchaseOrderStatus.BILLED,
            PurchaseOrderStatus.CANCELED,
        ],
    )
    def test_attach_allowed_at_any_status_without_reopening_edit(self, db, status):
        po = _po(db)
        po.status = status
        db.flush()
        version_before = po.version

        doc = _attach(db, po, source=DocumentSource.VIEW)

        assert doc.id is not None
        # Attaching is not an edit: the PO row/version must be untouched.
        assert po.version == version_before
        assert po.status == status

    def test_source_view_recorded(self, db):
        po = _po(db)
        doc = _attach(db, po, source=DocumentSource.VIEW)
        assert doc.source == DocumentSource.VIEW.value


# GET / SCOPING


class TestGetAndScoping:
    def test_get_document_round_trips(self, db):
        po = _po(db)
        doc = _attach(db, po)
        svc = PurchaseOrderService(db)
        assert svc.get_document(po.id, doc.id).id == doc.id

    def test_get_document_wrong_po_is_404(self, db):
        po_a = _po(db)
        po_b = _po(db, vendor=_vendor(db, name="Other Co"))
        doc = _attach(db, po_a)

        svc = PurchaseOrderService(db)
        # The document belongs to po_a; requesting it under po_b must 404,
        # never cross-read another PO's attachment by UUID.
        with pytest.raises(NotFoundException):
            svc.get_document(po_b.id, doc.id)

    def test_get_unknown_document_is_404(self, db):
        po = _po(db)
        svc = PurchaseOrderService(db)
        with pytest.raises(NotFoundException):
            svc.get_document(po.id, uuid.uuid4())


# DELETE (metadata) + storage_key handoff


class TestDeleteDocument:
    def test_delete_returns_storage_key_and_removes_row(self, db):
        po = _po(db)
        doc = _attach(db, po)
        doc_id, key = doc.id, doc.storage_key

        svc = PurchaseOrderService(db)
        returned = svc.delete_document(po.id, doc_id)

        assert returned == key  # router uses this to purge the object
        assert (
            db.query(PurchaseOrderDocument)
            .filter(PurchaseOrderDocument.id == doc_id)
            .first()
            is None
        )

    def test_delete_wrong_po_is_404(self, db):
        po_a = _po(db)
        po_b = _po(db, vendor=_vendor(db, name="Other Co"))
        doc = _attach(db, po_a)
        svc = PurchaseOrderService(db)
        with pytest.raises(NotFoundException):
            svc.delete_document(po_b.id, doc.id)


# DOCUMENTS PERSIST ACROSS PO EDITS


class TestPersistence:
    def test_documents_survive_po_update(self, db):
        po = _po(db)
        _attach(db, po)
        svc = PurchaseOrderService(db)

        svc.update(po.id, PurchaseOrderUpdate(notes="edited"), expected_version=1)
        db.expire_all()

        reloaded = svc.get_by_id(po.id)
        assert len(reloaded.documents) == 1


# DELETE-ON-ROLLBACK (no orphaned new object)


class TestRollbackCleanup:
    def test_new_object_deleted_if_transaction_rolls_back(self, db):
        po = _po(db)
        db.flush()
        storage = FakeStorage()
        key = f"purchase_orders/{po.id}/orphan.pdf"

        _attach(db, po, key=key, storage=storage)
        assert storage.deleted == []  # nothing inline

        db.rollback()
        # The freshly written object is removed because the row never committed.
        assert key in storage.deleted


# HARD-DELETE BLOB PURGE (no orphaned objects)


class TestHardDeletePurge:
    @pytest.mark.skipif(
        not USING_POSTGRES,
        reason="Hard delete loads the row FOR UPDATE (PostgreSQL).",
    )
    def test_hard_delete_purges_attachment_blobs(self, db):
        from app.common.audit import AuditEvent

        po = _po(db)
        doc = _attach(db, po)
        key = doc.storage_key
        po_id = po.id
        db.commit()

        fake = FakeStorage()
        svc = PurchaseOrderService(db)
        with patch(
            "app.modules.purchase_orders.service.storage_service",
            fake,
            create=True,
        ):
            # _purge_storage_objects imports storage_service lazily from
            # app.lib.storage, so patch there too.
            with patch("app.lib.storage.storage_service", fake):
                assert svc.delete(po_id) is False
                db.commit()

        # Object purged, no orphan left behind.
        assert key in fake.deleted
        # PO and its document are gone.
        assert (
            db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() is None
        )
        # The before-image audit row survives the delete.
        audit = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "purchase_order",
                AuditEvent.entity_id == po_id,
                AuditEvent.action == "hard_deleted",
            )
            .first()
        )
        assert audit is not None


# UPLOAD GUARD (oversize / unsupported)


class TestUploadGuard:
    @pytest.mark.no_db
    def test_unsupported_type_rejected(self):
        payload = io.BytesIO(b"MZ\x90\x00 fake executable")
        with pytest.raises(BadRequestException):
            validate_upload(payload, "malware.exe", "application/octet-stream")

    @pytest.mark.no_db
    def test_oversize_rejected(self):
        # A PDF-signed payload one byte over the cap must be rejected on size.
        big = io.BytesIO(b"%PDF-" + b"0" * MAX_UPLOAD_SIZE_BYTES)
        with pytest.raises(BadRequestException):
            validate_upload(big, "big.pdf", "application/pdf")

    @pytest.mark.no_db
    def test_valid_pdf_passes(self):
        ok = io.BytesIO(b"%PDF-1.7 minimal body")
        safe_basename, ext, size = validate_upload(ok, "receipt.pdf", "application/pdf")
        assert ext == ".pdf"
        assert size > 0
        assert safe_basename == "receipt.pdf"


# DUPLICATE DOES NOT COPY ATTACHMENTS


class TestDuplicateExcludesDocuments:
    def test_duplicate_has_no_documents(self, db):
        po = _po(db)
        _attach(db, po)
        svc = PurchaseOrderService(db)

        dup = svc.duplicate(po.id)
        db.expire_all()

        reloaded = svc.get_by_id(dup.id)
        assert reloaded.documents == []
