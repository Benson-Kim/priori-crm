import { useParams } from "react-router-dom";
import { QuoteForm } from "../forms/quote-form";

export default function EditQuotePage() {
    const { id } = useParams<{ id: string }>();

    return (
        <div className="h-full w-full">
            <QuoteForm quoteId={id} />
        </div>
    );
}
