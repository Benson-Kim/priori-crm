/**
 * DeskProgressBar
 *
 * The 8px track shared by the stage breakdown and the rep-quota rows. Values
 * are clamped, since a rep can exceed quota; a hairline minimum keeps a tiny
 * non-zero share visible.
 */

interface DeskProgressBarProps {
    /** Share of the track to fill, 0 to 1. Values above 1 render as full. */
    value: number;
    /** Fill colour. Rep rows are tinted per rep, so this is a raw CSS colour. */
    color: string;
    label: string;
}

export function DeskProgressBar({ value, color, label }: Readonly<DeskProgressBarProps>) {
    const percent = Math.min(Math.max(value, 0), 1) * 100;

    return (
        <div
            className="h-2 w-full overflow-hidden rounded-full bg-desk-border"
            role="progressbar"
            aria-label={label}
            aria-valuenow={Math.round(percent)}
            aria-valuemin={0}
            aria-valuemax={100}
        >
            <div
                className="h-full rounded-full transition-[width] duration-300"
                style={{ width: `${percent > 0 ? Math.max(percent, 1.5) : 0}%`, backgroundColor: color }}
            />
        </div>
    );
}
