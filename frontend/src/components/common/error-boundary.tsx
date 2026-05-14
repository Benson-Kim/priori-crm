import React, { Component, type ReactNode } from 'react';
import { Button } from '@/components/ui/Button';

interface ErrorBoundaryProps {
    children: ReactNode;
}

interface ErrorBoundaryState {
    hasError: boolean;
    error: Error | null;
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
    constructor(props: ErrorBoundaryProps) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error): ErrorBoundaryState {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
        console.error('Error Boundary Caught an Error:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className='flex items-center justify-center flex-col min-h-screen'>
                    <div className="glass-effect bg-white backdrop-blur-lg rounded-3xl p-12 max-w-md w-full text-center shadow-2xl border border-black/10 animate-slide-up">

                        {/* <!-- Error Icon --> */}
                        <div className="w-20 h-20 mx-auto mb-8 bg-linear-to-br from-red-500 to-pink-600 rounded-full flex items-center justify-center animate-pulse-custom">
                            <svg className="w-10 h-10 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.732 16.5c-.77.833.192 2.5 1.732 2.5z"></path>
                            </svg>
                        </div>

                        {/* <!-- Title --> */}
                        <h1 className="text-3xl font-bold text-black dark:text-gray-100 mb-4">
                            Oops! Something went wrong
                        </h1>

                        {/* <!-- Description --> */}
                        <p className="text-gray-300 dark:text-gray-400 text-lg leading-relaxed mb-6">
                            We've encountered an unexpected error. Don't worry - the error has been reported.
                        </p>

                        {/* <!-- Error Code --> */}
                        {/* <div className="bg-gray-800/50 dark:bg-gray-900/50 border border-gray-600/50 dark:border-gray-700/50 rounded-xl p-4 mb-8">
              <p className="text-gray-400 dark:text-gray-500 text-sm font-mono">
                Error ID: #{this.state.errorCode || 'N/A'}
              </p>
            </div> */}

                        {/* <!-- Action Buttons --> */}
                        <div className="flex flex-col sm:flex-row gap-4 justify-center">
                            <Button onClick={() => globalThis.location.reload()} >
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                                </svg>
                                Try Again
                            </Button>

                            <Button variant={"outline"} onClick={() => globalThis.location.href = "/dashboard"} >
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>
                                </svg>
                                Go Back
                            </Button>
                        </div>

                        {/* <!-- Status Indicator --> */}
                        <div className="mt-8 inline-flex items-center gap-2 bg-green-500/20 dark:bg-green-400/20 text-green-400 dark:text-green-300 px-4 py-2 rounded-full text-sm border border-green-500/30 dark:border-green-400/30">
                            <div className="w-2 h-2 bg-green-500 dark:bg-green-400 rounded-full animate-pulse"></div>
                            Error reported - Issue being resolved
                        </div>

                        {/* <!-- Additional Help --> */}
                        <div className="mt-6 text-gray-400 dark:text-gray-500 text-sm">
                            Need help?
                            <a href="/help" className="text-blue-400 hover:text-blue-300 underline transition-colors">
                                Contact Support
                            </a>
                        </div>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;