import { useParams } from "react-router-dom";
import { InvoiceForm } from "../forms/invoice-form";

export default function EditInvoicePage() {
    const { id } = useParams<{ id: string }>();

    return <InvoiceForm invoiceId={id} />;
}


