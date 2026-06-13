/**
 * MetricCard — shared presentational primitive.
 *
 * Displays a single KPI tile: a label, a formatted value, and an optional
 * delta badge. Used by the Dashboard overview cards and the Statements
 * OverviewComponent. Lives in `components/ui` (not under any page) so
 * neither page imports from the other's directory.
 */

export interface MetricChange {
  text: string;
  tone: "positive" | "negative" | "neutral";
}

interface MetricCardProps {
  label: string;
  value: string;
  /** Pass `null` while loading so the badge renders a dash placeholder. */
  change: MetricChange | null;
}

const TONE_CLASSES: Record<MetricChange["tone"], string> = {
  positive: "bg-success-50 text-success-600",
  negative: "bg-error-50 text-error-600",
  neutral: "bg-gray-100 text-gray-500",
};

export const MetricCard = ({ label, value, change }: MetricCardProps) => (
  <div className="relative flex flex-col justify-between rounded-2xl border border-gray-200 bg-linear-to-br from-white/25 via-white/10 to-white/05 px-6 py-3 backdrop-blur-md shadow-[4px_4px_4px_0px_rgba(0,0,0,0.02)]">
    <p className="text-gray-500 text-[18px] py-3">{label}</p>
    <div className="py-3 flex items-center justify-between gap-3">
      <p className="font-bold text-gray-800 text-2xl">{value}</p>
      <span
        className={`text-sm font-semibold px-2 py-1 rounded-full ${
          change ? TONE_CLASSES[change.tone] : TONE_CLASSES.neutral
        }`}
      >
        {change ? change.text : "\u2014"}
      </span>
    </div>
  </div>
);
