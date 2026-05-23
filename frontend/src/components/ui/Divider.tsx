interface DividerProps {
    className?: string;
}

export function Divider({ className }: DividerProps) {
    return <div className={`h-px bg-purple-25 w-full ${className}`} />;
}