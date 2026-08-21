import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import BottomRightCorner from "@/assets/bottom-right-corner.svg";
import TopLeftCorner from "@/assets/top-left-corner.svg";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { ApiError } from "@/lib/api";
import { validatePasswordStrength } from "@/lib/utils";
import { resetPassword } from "@/services/authApi";
import { ArrowLeft, Eye, EyeOff } from "lucide-react";

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = useMemo(() => searchParams.get("token") ?? "", [searchParams]);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  // A missing token means the user opened /reset-password directly rather than
  // via the emailed link; there is nothing to submit, so guide them back.
  const hasToken = token.length > 0;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    const policyError = validatePasswordStrength(password);
    if (policyError) {
      setError(policyError);
      return;
    }
    if (password !== confirmPassword) {
      setError("The passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await resetPassword(token, password);
      setDone(true);
      // Brief confirmation, then to the sign-in page to log in afresh
      // (the reset endpoint deliberately issues no tokens).
      setTimeout(() => navigate("/login", { replace: true }), 1500);
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

  const makeEyeToggle = (visible: boolean, toggle: () => void) => (
    <button
      type="button"
      onClick={toggle}
      className="text-priori-purple hover:text-priori-light-purple-2 cursor-pointer"
      aria-label={visible ? "Hide password" : "Show password"}
      tabIndex={-1}
    >
      {visible ? <EyeOff className="size-5" /> : <Eye className="size-5" />}
    </button>
  );

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
              src="/logo.svg"
              alt="Business Central"
              className="h-20 object-contain"
            />
          </div>

          <div className="text-center">
            <h1 className="text-2xl font-bold text-cocoa mb-2">
              {done ? "Password reset" : "Set a new password"}
            </h1>
            <p className="text-gray-700 text-xl">
              {done
                ? "Your password has been reset. Redirecting you to sign in..."
                : "Choose a strong new password for your account."}
            </p>
          </div>
        </div>

        {!hasToken ? (
          <div className="flex flex-col gap-8">
            <p role="alert" className="text-gray-700 text-base leading-6 text-center">
              This reset link is missing or invalid. Request a new one to
              continue.
            </p>
            <Button
              asChild
              variant="primary"
              size="lg"
              className="w-full"
            >
              <Link to="/forgot-password">Request a new link</Link>
            </Button>
          </div>
        ) : done ? (
          <Button
            asChild
            variant="primary"
            size="lg"
            className="w-full"
          >
            <Link to="/login">Go to sign in</Link>
          </Button>
        ) : (
          <form className="space-y-8" onSubmit={handleSubmit} noValidate>
            <div className="space-y-2">
              <Label htmlFor="password" className="text-cocoa py-1 font-bold text-lg leading-7">New Password</Label>
              <Input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                placeholder="Enter your new password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                suffix={makeEyeToggle(showPassword, () =>
                  setShowPassword((v) => !v)
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="confirm_password" className="text-cocoa py-1 font-bold text-lg leading-7">Confirm Password</Label>
              <Input
                id="confirm_password"
                name="confirm_password"
                type={showConfirmPassword ? "text" : "password"}
                autoComplete="new-password"
                placeholder="Re-enter your new password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={submitting}
                suffix={makeEyeToggle(showConfirmPassword, () =>
                  setShowConfirmPassword((v) => !v)
                )}
              />
            </div>

            <p className="text-gray-600 text-sm leading-5 -mt-4">
              Use at least 8 characters with one letter and one number.
            </p>

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
              className="w-full"
            >
              Reset password
            </Button>

            <Link
              to="/login"
              className="flex items-center justify-center gap-2 text-base text-priori-purple hover:underline"
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
