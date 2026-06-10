import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import BottomRightCorner from "@/assets/bottom-right-corner.svg";
import TopLeftCorner from "@/assets/top-left-corner.svg";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { useAuth } from "@/hooks/auth-context";
import { ApiError } from "@/lib/api";

interface LocationState {
  from?: { pathname?: string };
}

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const redirectTo =
    (location.state as LocationState | null)?.from?.pathname ?? "/";

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    if (!email.trim() || !password) {
      setError("Enter your email and password to continue.");
      return;
    }

    setSubmitting(true);
    try {
      await login(email.trim(), password);
      // Carry the email + intended destination to the OTP step.
      navigate("/verify-otp", {
        state: { email: email.trim(), redirectTo },
      });
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
    <div className="min-h-screen bg-white relative flex items-center justify-center overflow-hidden">
      {/* Background Shapes */}
      <img
        src={TopLeftCorner}
        alt=""
        className="absolute top-0 left-0 w-64 md:w-96 pointer-events-none"
      />
      <img
        src={BottomRightCorner}
        alt=""
        className="absolute bottom-0 right-0 w-64 md:w-96 pointer-events-none"
      />

      {/* Login Card */}
      <div className="w-full max-w-lg bg-gray-100 border border-gray-200 rounded-lg flex flex-col gap-8 p-12 z-10">
        <div className="space-y-8">
          <div className="flex justify-center">
            <img
              src="/Logo Priori.svg"
              alt="Priori Technologies"
              className="h-20 object-contain"
            />
          </div>

          <div className="text-center">
            <h1 className="text-[24px] font-bold text-cocoa mb-2">Welcome back</h1>
            <p className="text-gray-700 text-[20px]">
              Sign in to manage invoicing instantly
            </p>
          </div>
        </div>

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

          <div className="space-y-2">
            <Label htmlFor="password" className="text-cocoa py-1 font-bold text-[18px] leading-7">Password</Label>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
          </div>

          {error && (
            <p role="alert" className="text-sm text-red-600 -mt-4">
              {error}
            </p>
          )}

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <input type="checkbox" name="remember_me" id="remember_me" className="h-6 w-6 border border-gray-500 accent-priori-purple cursor-pointer" />
              <Label htmlFor="remember_me" className="text-gray-700 text-[16px] leading-6">Remember me</Label>
            </div>
            <Link
              to="#"
              className="text-[16px] text-priori-purple hover:underline"
            >
              Forgot Password?
            </Link>
          </div>
          <Button
            type="submit"
            variant="primary"
            size="lg"
            loading={submitting}
            className="w-full text-[18px] font-bold"
          >
            Continue
          </Button>
        </form>
      </div>
    </div>
  );
}
