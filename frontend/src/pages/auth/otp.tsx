import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import BottomRightCorner from "@/assets/bottom-right-corner.svg";
import TopLeftCorner from "@/assets/top-left-corner.svg";
import { Button } from "@/components/ui/Button";
import { OTPInput } from "@/components/ui/OTPInput";
import { useAuth } from "@/hooks/auth-context";
import { ApiError } from "@/lib/api";
import { OTP_EXPIRY_SECONDS, OTP_LENGTH } from "@/lib/constants";
import { maskEmail } from "@/lib/utils";
import { ArrowLeft } from "lucide-react";

interface OTPLocationState {
  email?: string;
  password?: string;
  redirectTo?: string;
}

function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const s = (seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export default function OTPPage() {
  const { verifyOtp, resendOtp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state as OTPLocationState | null) ?? {};
  const email = state.email ?? "";
  const redirectTo = state.redirectTo ?? "/";

  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(OTP_EXPIRY_SECONDS);

  // No email in router state means the user deep-linked here; send them back.
  useEffect(() => {
    if (!email) {
      navigate("/login", { replace: true });
    }
  }, [email, navigate]);

  // Expiry countdown.
  useEffect(() => {
    if (secondsLeft <= 0) return;
    const id = setInterval(() => setSecondsLeft((s) => s - 1), 1000);
    return () => clearInterval(id);
  }, [secondsLeft]);

  async function submitCode(value: string) {
    if (value.length !== OTP_LENGTH) {
      setError("Enter the complete 6-digit code.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await verifyOtp(email, value);
      navigate(redirectTo, { replace: true });
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

  async function handleResend() {
    setError(null);
    setSubmitting(true);
    try {
      await resendOtp(email);
      setSecondsLeft(OTP_EXPIRY_SECONDS);
      setCode("");
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : "Failed to resend code. Please try again.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }


  return (
    <div className="min-h-screen bg-white relative flex items-center justify-center overflow-hidden">

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

      {/* OTP Card */}
      <div className="w-full max-w-156 bg-gray-100 border border-gray-200 rounded-4xl flex flex-col gap-8 p-12 z-10">
        <div className="space-y-8">
          <div className="flex justify-center">
            <img src="/Logo Priori.svg" alt="Business Central"
              className="h-20 object-contain"
            />
          </div>

          <div className="text-center">
            <h3 className="text-[24px] font-bold text-cocoa mb-2">Verify it&apos;s you</h3>
            <p className="text-gray-700 text-[20px] whitespace-nowrap">
              A 6-digit code was sent to{" "}
              <span className="font-bold">{email ? maskEmail(email) : "your email"}</span>
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-8">
          <div className="mx-auto w-full max-w-md">
            <OTPInput
              onComplete={(value) => {
                setCode(value);
                void submitCode(value);
              }}
              error={error ?? undefined}
              disabled={submitting}
            />
          </div>

          <div className="py-1">
            <div className="text-[18px] leading-7 text-gray-700">
              {secondsLeft > 0 ? (
                <div className="py-1 flex flex-wrap items-center gap-2">
                  <p className="font-normal m-0">
                    <span className="font-bold">Code expires in {formatCountdown(secondsLeft)}{" • "}</span>
                    Didn&apos;t receive OTP?
                  </p>
                  {/* Resend is only available once the current code has
                      expired (it stays valid for the full 5 minutes), so it is
                      shown disabled while the countdown is running. */}
                  <button type="button" disabled
                    aria-disabled="true"
                    title="You can request a new code once this one expires"
                    className="text-gray-400 opacity-50 cursor-not-allowed"
                  >
                    Resend OTP
                  </button>
                </div>
              ) : (
                  <div className="py-1 flex flex-wrap items-center justify-center gap-2">
                    <span className="text-danger font-normal">Code expired.</span>
                    <button type="button" disabled={submitting}
                      onClick={() => void handleResend()}
                      className="text-sky-blue disabled:opacity-50 cursor-pointer"
                    >
                      Resend OTP
                    </button>
                  </div>
              )}
            </div>
          </div>

          <div className="flex items-center justify-between gap-4">
            <Button type="button" variant="outline" size="lg" disabled={submitting}
              onClick={() => navigate("/login")}
              className="w-full text-[18px] font-bold"
            >
              <ArrowLeft />
              Back
            </Button>
            <Button type="button" variant="primary" size="lg" loading={submitting}
              onClick={() => void submitCode(code)}
              className="w-full text-[18px] font-bold"
            >
              Continue
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
