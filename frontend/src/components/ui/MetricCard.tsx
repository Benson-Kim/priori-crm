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
  /** Pass `null` (or a change with empty `text`) to render no badge at all. */
  change: MetricChange | null;
}

const TONE_CLASSES: Record<MetricChange["tone"], string> = {
  positive: "bg-success-50 text-success-600",
  negative: "bg-error-50 text-error-600",
  neutral: "bg-gray-100 text-gray-500",
};

export const MetricCard = ({ label, value, change }: MetricCardProps) => (
  <div className="relative flex flex-col justify-between rounded-2xl border border-gray-200 bg-white p-5 gap-2 backdrop-blur-md shadow-[4px_4px_4px_0px_rgba(0,0,0,0.02)]">
    {/*
     * The delta sits beside the label, not beside the amount. Sharing a row
     * with the badge left the value about 126px in a four-up card, which is
     * not enough for "Ksh 1,250,000.00" at any readable size \u2014 it wrapped, and
     * clamping it to one line only traded the wrap for hidden digits.
     */}
    <div className="flex items-start justify-between gap-3">
      <p className="text-gray-500 text-sm leading-4.5">{label}</p>

      {change && change.text && (
        <span
          className={`shrink-0 text-sm font-semibold p-2 rounded-lg ${TONE_CLASSES[change.tone]}`}
        >
          {change.text}
        </span>
      )}
    </div>
    {/*
     * One line, always: "Ksh" must never sit above its digits. The clamp is
     * driven by the viewport rather than by the content, so every card in a
     * row renders at the same size \u2014 a content-fitted size would make each
     * tile differ and read as a bug. truncate + title is the last resort for
     * an amount too long even at the small end.
     */}
    <p
      className="min-w-0 truncate whitespace-nowrap font-bold text-gray-800 leading-8 text-[clamp(0.875rem,1.35vw,1.5rem)]"
      title={value}
    >
      {value}
    </p>
  </div>
);
