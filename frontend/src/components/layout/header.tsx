import { Bell, ChevronDown, Sun } from "lucide-react";

type HeaderProps = {
    title: string;
    description?: string;
};

export function Header({ title, description }: Readonly<HeaderProps>) {
    return (
        <header className="bg-[linear-gradient(90deg,#F9FAFB_50.33%,rgba(217,226,235,0.2)_100.65%)] px-4 py-3 rounded-t-lg border border-gray-200 flex items-center justify-between">
            <div className="">
                <h1 className="text-2xl font-bold tracking-normal leading-8 text-content-primary">{title}</h1>
                {description && <p className="text-base text-content-secondary leading-6">{description}</p>}
            </div>

            <div className="flex items-center gap-3 shrink-0">
                <button className="relative p-2 text-gray-500 hover:text-content-primary transition-colors cursor-pointer rounded-full hover:bg-black/5">
                    <Bell size={24} fill="#667085" />
                    <span className="absolute top-2 right-2 h-2.5 w-2.5 rounded-full bg-danger border-2 border-ghostwhite"></span>
                </button>
                <button className="py-2 px-1 text-gray-500 hover:text-content-primary transition-colors cursor-pointer rounded-full hover:bg-black/5">
                    <Sun size={24} />
                </button>

                <div className="flex items-center gap-2 ml-1 cursor-pointer hover:bg-black/5 rounded-full transition-colors">
                    <div className="h-8 w-8 rounded-full bg-sky-blue-900 flex items-center justify-center gap-1 overflow-hidden shrink-0">
                        <img
                            src="https://api.dicebear.com/7.x/avataaars/svg?seed=Felix&backgroundColor=b6e3f4"
                            alt="User avatar"
                            className="h-full w-full object-cover"
                        />
                    </div>
                    <ChevronDown size={24} className="text-gray-500 mr-1 shrink-0" />
                </div>
            </div>
        </header>
    );
}

export default Header;