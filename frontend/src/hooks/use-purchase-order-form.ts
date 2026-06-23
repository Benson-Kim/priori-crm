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
        // Currency, recurring and compliance ref are no longer collected on
        // the form: currency is derived from the vendor server-side, and the
        // other two were removed from the PO create flow.
        const createPayload: PurchaseOrderCreatePayload = {
          vendorId: payload.vendorId,
          orderDate: payload.orderDate,
          deliveryDate: payload.deliveryDate,
          notes: payload.notes,
          termsAndConditions: payload.termsAndConditions,
          lineItems,
        };
        const created = await createPurchaseOrder(createPayload);
        idToNavigate = created.id;
      }

      navigate(`/purchase-orders`);
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
