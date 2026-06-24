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

/** Options for a single save invocation. */
export interface SaveOptions {
  /**
   * When true the form does NOT navigate away after a successful save and
   * instead resolves the persisted PO. Used by the editor's action dropdown
   * (Download PDF / Mark as Sent / Send / Delete) so the caller can run the
   * chosen action against the freshly-saved PO id.
   */
  skipNavigate?: boolean;
}

interface UsePurchaseOrderFormReturn {
  initialData: PurchaseOrderResponse | null;
  isLoading: boolean;
  isFetching: boolean;
  error: string | null;
  isRestricted: boolean;
  handleSave: (
    payload: PurchaseOrderPayload,
    options?: SaveOptions
  ) => Promise<PurchaseOrderResponse>;
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

  const handleSave = async (
    payload: PurchaseOrderPayload,
    options?: SaveOptions
  ): Promise<PurchaseOrderResponse> => {
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

      let saved: PurchaseOrderResponse;

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
        saved = await updatePurchaseOrder(
          purchaseOrderId,
          updatePayload,
          initialData.version
        );
        // Keep the in-memory version fresh so a follow-up action-driven save
        // in the same session doesn't 409 on a stale expected version.
        setInitialData(saved);
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
        saved = await createPurchaseOrder(createPayload);
      }

      // Plain "Save & Continue" returns to the list; an action-driven save
      // (skipNavigate) leaves navigation to the caller so it can run the
      // chosen action against the persisted PO first.
      if (!options?.skipNavigate) {
        navigate(`/purchase-orders`);
      }

      return saved;
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
