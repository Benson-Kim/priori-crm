import type { PurchaseOrderPayload } from "@/components/documents/PurchaseOrderEditor";
import {
  createPurchaseOrder,
  getPurchaseOrder,
  updatePurchaseOrder,
  type PurchaseOrderCreatePayload,
  type PurchaseOrderResponse,
  type PurchaseOrderUpdatePayload,
} from "@/services/purchaseOrderApi";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface UsePurchaseOrderFormReturn {
  initialData: PurchaseOrderResponse | null;
  isLoading: boolean;
  isFetching: boolean;
  error: string | null;
  isRestricted: boolean;
  handleSave: (payload: PurchaseOrderPayload) => Promise<void>;
  handleCancel: () => void;
}

export function usePurchaseOrderForm(
  purchaseOrderId?: string
): UsePurchaseOrderFormReturn {
  const [isFetching, setIsFetching] = useState(!!purchaseOrderId);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initialData, setInitialData] = useState<PurchaseOrderResponse | null>(
    null
  );
  const navigate = useNavigate();

  const fetchPurchaseOrder = useCallback(async () => {
    if (!purchaseOrderId) return;

    try {
      setIsFetching(true);
      const data = await getPurchaseOrder(purchaseOrderId);

      // Only DRAFT purchase orders are editable; otherwise bounce to View.
      if (!data.is_editable) {
        navigate(`/purchase-orders/${purchaseOrderId}`, { replace: true });
        return;
      }

      setInitialData(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load purchase order"
      );
    } finally {
      setIsFetching(false);
    }
  }, [purchaseOrderId, navigate]);

  useEffect(() => {
    if (purchaseOrderId)
      void (async () => {
        await fetchPurchaseOrder();
      })();
  }, [purchaseOrderId, fetchPurchaseOrder]);

  const handleCancel = () => {
    navigate(
      purchaseOrderId ? `/purchase-orders/${purchaseOrderId}` : "/purchase-orders"
    );
  };

  const handleSave = async (payload: PurchaseOrderPayload) => {
    try {
      setIsLoading(true);
      setError(null);

      const lineItems = payload.lineItems.map((li) => ({
        itemName: li.itemName,
        description: li.description,
        quantity: li.quantity,
        unitPrice: li.unitPrice,
        taxType: li.taxType,
      }));

      let idToNavigate = purchaseOrderId;

      if (purchaseOrderId && initialData) {
        const updatePayload: PurchaseOrderUpdatePayload = {
          vendorId: payload.vendorId,
          orderDate: payload.orderDate,
          deliveryDate: payload.deliveryDate,
          isRecurring: payload.isRecurring,
          complianceRef: payload.complianceRef,
          notes: payload.notes,
          termsAndConditions: payload.termsAndConditions,
          lineItems,
        };
        // Pass the loaded version for optimistic-lock (stale write -> 409).
        await updatePurchaseOrder(
          purchaseOrderId,
          updatePayload,
          initialData.version
        );
      } else {
        const createPayload: PurchaseOrderCreatePayload = {
          vendorId: payload.vendorId,
          orderDate: payload.orderDate,
          deliveryDate: payload.deliveryDate,
          currency: payload.currency,
          isRecurring: payload.isRecurring,
          complianceRef: payload.complianceRef,
          notes: payload.notes,
          termsAndConditions: payload.termsAndConditions,
          lineItems,
        };
        const created = await createPurchaseOrder(createPayload);
        idToNavigate = created.id;
      }

      // Upload any queued documents after the save succeeds.
      if (payload.files && payload.files.length > 0 && idToNavigate) {
        const { uploadPurchaseOrderDocument } = await import(
          "@/services/purchaseOrderApi"
        );
        await Promise.all(
          payload.files.map((file) =>
            uploadPurchaseOrderDocument(idToNavigate!, file, "form")
          )
        );
      }

      navigate(`/purchase-orders/${idToNavigate}`);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : purchaseOrderId
            ? "Failed to update purchase order"
            : "Failed to create purchase order"
      );
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  return {
    initialData,
    isFetching,
    isLoading,
    error,
    isRestricted: !initialData?.is_editable,
    handleSave,
    handleCancel,
  };
}
