import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { getCustomer } from "@/lib/customerApi";
import { formatCurrency } from "@/lib/utils";
import { ArrowLeft, Edit, Mail, Phone, Globe, MapPin } from "lucide-react";
import { useEffect, useState, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function CustomerDetailsPage() {
    const { id } = useParams<{ id: string }>();
    const [customer, setCustomer] = useState<any>(null);
    const [isFetching, setIsFetching] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const navigate = useNavigate();

    const fetchCustomer = useCallback(async () => {
        if (!id) return;
        try {
            setIsFetching(true);
            const data = await getCustomer(id);
            setCustomer(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch customer details");
        } finally {
            setIsFetching(false);
        }
    }, [id]);

    useEffect(() => {
        fetchCustomer();
    }, [fetchCustomer]);

    if (isFetching) {
        return (
            <div className="flex items-center justify-center h-64 text-gray-400">
                Loading customer details...
            </div>
        );
    }

    if (error || !customer) {
        return (
            <div className="p-8 text-center">
                <p className="text-red-500 mb-4">{error || "Customer not found"}</p>
                <Button onClick={() => navigate("/customers")}>Back to Customers</Button>
            </div>
        );
    }

    const { customer: details, financial_summary, invoices } = customer;

    return (
        <div className="space-y-6">
            {/* Header / Breadcrumbs */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => navigate("/customers")}
                        className="p-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200"
                    >
                        <ArrowLeft size={20} />
                    </button>
                    <div>
                        <h2 className="text-2xl font-bold text-gray-900">
                            {details.company_name || `${details.first_name} ${details.last_name}`}
                        </h2>
                        <div className="flex items-center gap-2 mt-1">
                            <Badge variant={details.status.toLowerCase() as any}>
                                {details.status}
                            </Badge>
                            <span className="text-sm text-gray-500">ID: {details.id.split('-')[0]}...</span>
                        </div>
                    </div>
                </div>
                <Button 
                    onClick={() => navigate(`/customers/${details.id}/edit`)}
                    className="bg-priori-purple hover:bg-priori-purple/90 text-white"
                >
                    <Edit size={16} className="mr-2" />
                    Edit Customer
                </Button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Left Column: Contact & Info */}
                <div className="space-y-6">
                    <Card className="p-6 space-y-4">
                        <h3 className="font-bold text-gray-900 border-b pb-2">Contact Information</h3>
                        <div className="space-y-3">
                            <div className="flex items-start gap-3">
                                <Mail size={18} className="text-gray-400 mt-1" />
                                <div>
                                    <p className="text-xs text-gray-500">Email Address</p>
                                    <p className="text-sm font-medium">{details.email}</p>
                                </div>
                            </div>
                            <div className="flex items-start gap-3">
                                <Phone size={18} className="text-gray-400 mt-1" />
                                <div>
                                    <p className="text-xs text-gray-500">Phone Number</p>
                                    <p className="text-sm font-medium">{details.phone}</p>
                                </div>
                            </div>
                            {details.website && (
                                <div className="flex items-start gap-3">
                                    <Globe size={18} className="text-gray-400 mt-1" />
                                    <div>
                                        <p className="text-xs text-gray-500">Website</p>
                                        <a href={details.website} target="_blank" rel="noreferrer" className="text-sm font-medium text-priori-purple hover:underline">
                                            {details.website}
                                        </a>
                                    </div>
                                </div>
                            )}
                        </div>
                    </Card>

                    <Card className="p-6 space-y-4">
                        <h3 className="font-bold text-gray-900 border-b pb-2">Billing Address</h3>
                        <div className="flex items-start gap-3">
                            <MapPin size={18} className="text-gray-400 mt-1" />
                            <div className="text-sm space-y-1">
                                <p>{details.address}</p>
                                {details.address2 && <p>{details.address2}</p>}
                                <p>{details.city}, {details.province} {details.postal_code}</p>
                                <p>{details.country}</p>
                            </div>
                        </div>
                    </Card>
                </div>

                {/* Right Column: Financials & History */}
                <div className="lg:col-span-2 space-y-6">
                    {/* Financial Summary Cards */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <Card className="p-4 bg-purple-50 border-purple-100">
                            <p className="text-xs text-purple-600 font-semibold mb-1 uppercase tracking-wider">Balance Due</p>
                            <p className="text-xl font-bold text-purple-900">{formatCurrency(details.balance, details.currency)}</p>
                        </Card>
                        <Card className="p-4 bg-red-50 border-red-100">
                            <p className="text-xs text-red-600 font-semibold mb-1 uppercase tracking-wider">Overdue</p>
                            <p className="text-xl font-bold text-red-900">{formatCurrency(financial_summary.overdue, details.currency)}</p>
                        </Card>
                        <Card className="p-4 bg-green-50 border-green-100">
                            <p className="text-xs text-green-600 font-semibold mb-1 uppercase tracking-wider">Total Paid</p>
                            <p className="text-xl font-bold text-green-900">{formatCurrency(financial_summary.total_paid, details.currency)}</p>
                        </Card>
                        <Card className="p-4 bg-blue-50 border-blue-100">
                            <p className="text-xs text-blue-600 font-semibold mb-1 uppercase tracking-wider">Total Invoiced</p>
                            <p className="text-xl font-bold text-blue-900">{formatCurrency(financial_summary.total_invoiced, details.currency)}</p>
                        </Card>
                    </div>

                    {/* Placeholder for Tabs (Invoices, Statements, etc) */}
                    <Card className="p-6">
                        <h3 className="font-bold text-gray-900 mb-4">Recent Invoices</h3>
                        {invoices.length > 0 ? (
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm text-left">
                                    <thead>
                                        <tr className="border-b text-gray-500 uppercase text-[10px] tracking-wider">
                                            <th className="py-3 px-2">Invoice #</th>
                                            <th className="py-3 px-2">Date</th>
                                            <th className="py-3 px-2">Amount</th>
                                            <th className="py-3 px-2">Status</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {invoices.slice(0, 5).map((inv: any) => (
                                            <tr key={inv.id} className="border-b hover:bg-gray-50 transition-colors">
                                                <td className="py-3 px-2 font-medium text-priori-purple">{inv.invoice_number}</td>
                                                <td className="py-3 px-2 text-gray-600">{inv.date}</td>
                                                <td className="py-3 px-2 font-semibold">{formatCurrency(inv.amount, details.currency)}</td>
                                                <td className="py-3 px-2">
                                                    <span className={`px-2 py-1 rounded-full text-[10px] font-bold uppercase ${
                                                        inv.status.includes('Overdue') ? 'bg-red-100 text-red-700' : 
                                                        inv.status === 'Paid' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'
                                                    }`}>
                                                        {inv.status}
                                                    </span>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <p className="text-center py-8 text-gray-400">No invoices found for this customer.</p>
                        )}
                    </Card>
                </div>
            </div>
        </div>
    );
}
