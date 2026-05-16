import { useParams } from "react-router-dom";
import { CustomerForm } from "../forms/customer-form";

export default function EditCustomerPage() {
    const { id } = useParams<{ id: string }>();

    return <CustomerForm customerId={id} />;
}