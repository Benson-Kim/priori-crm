import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import BottomRightCorner from "@/assets/bottom-right-corner.svg";
import TopLeftCorner from "@/assets/top-left-corner.svg";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { ApiError } from "@/lib/api";
import { forgotPassword } from "@/services/authApi";
import { ArrowLeft } from "lucide-react";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Enumeration-safe: once submitted we always show the same confirmation,
  // never revealing whether the address has an account.
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    if (!email.trim()) {
      setError("Enter your email address to continue.");
      return;
    }

    setSubmitting(true);
    try {
      await forgotPassword(email.trim());
      // The backend response is intentionally generic; show the confirmation
      // regardless of whether the account exists.
      setSubmitted(true);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : "Something went wrong. Please try again.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen w-screen bg-white relative flex items-center justify-center overflow-hidden">

      {/* Top Left Background Shape */}
      <img src={TopLeftCorner} alt="" aria-hidden="true"
        className="absolute left-0 top-0 pointer-events-none select-none"
        style={{ width: 183, height: 322 }}
      />

      {/* Bottom Right Background Shape */}
      <img src={BottomRightCorner} alt="" aria-hidden="true"
        className="absolute right-0 bottom-0 pointer-events-none select-none"
        style={{ width: 257, height: 394 }}
      />

      {/* Card */}
      <div className="w-full max-w-156 bg-gray-100 border border-gray-200 rounded-4xl flex flex-col gap-8 p-12 z-10">

        <div className="space-y-8">
          <div className="flex justify-center">
            <img
              src="/Logo Priori.svg"
              alt="Priori Technologies"
              className="h-20 object-contain"
            />
          </div>

          <div className="text-center">
            <h1 className="text-[24px] font-bold text-cocoa mb-2">
              {submitted ? "Check your email" : "Forgot your password?"}
            </h1>
            <p className="text-gray-700 text-[20px]">
              {submitted
                ? "If an account exists for that email, a password reset link is on its way."
                : "Enter your email and we'll send you a link to reset it."}
            </p>
          </div>
        </div>

        {submitted ? (
          <div className="flex flex-col gap-8">
            <p className="text-gray-700 text-[16px] leading-6 text-center">
              The link expires shortly for your security. Didn&apos;t get it?
              Check your spam folder, or try again.
            </p>
            <div className="flex items-center justify-between gap-4">
              <Button
                type="button"
                variant="outline"
                size="lg"
                onClick={() => {
                  setSubmitted(false);
                  setEmail("");
                }}
                className="w-full text-[18px] font-bold"
              >
                Try another email
              </Button>
              <Button
                asChild
                variant="primary"
                size="lg"
                className="w-full text-[18px] font-bold"
              >
                <Link to="/login">Back to sign in</Link>
              </Button>
            </div>
          </div>
        ) : (
          <form className="space-y-8" onSubmit={handleSubmit} noValidate>
            <div className="space-y-2">
              <Label htmlFor="email" className="text-cocoa py-1 font-bold text-[18px] leading-7">Email Address</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                placeholder="Enter your email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
              />
            </div>

            {error && (
              <p role="alert" className="text-sm text-red-600 -mt-4">
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              loading={submitting}
              className="w-full text-[18px] font-bold"
            >
              Send reset link
            </Button>

            <Link
              to="/login"
              className="flex items-center justify-center gap-2 text-[16px] text-priori-purple hover:underline"
            >
              <ArrowLeft className="size-4" />
              Back to sign in
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
