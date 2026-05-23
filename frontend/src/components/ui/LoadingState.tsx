import { Loader2 } from "lucide-react";

interface LoadingStateProps {
    message?: string;
    className?: string;
}

export function LoadingState({ message = "Loading...", className = "h-40" }: Readonly<LoadingStateProps>) {
    return (
        <div className={`flex flex-col items-center justify-center text-gray-400 gap-3 ${className}`}>
            <Loader2 className="animate-spin text-priori-purple" size={32} />
            <span className="text-sm font-medium">{message}</span>
        </div>
    );
}
