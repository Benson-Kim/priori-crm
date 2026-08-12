/**
 * Deal stage vocabulary.
 *
 * Lives in lib/ rather than in either the service layer or components/ui, so
 * the shared stage components and the Sales Desk services can both depend on
 * it without depending on each other.
 *
 * A stage and a status are different things: a deal sits in one of the four
 * selling stages while it is open, and then it closes won or lost. The design
 * draws five steps because the close is the fifth, not because there is a
 * fifth stage.
 */

export const DEAL_STAGES = [
    "Activation",
    "Qualification",
    "Proposal & Quote",
    "Negotiation",
] as const;

export type DealStage = (typeof DEAL_STAGES)[number];

export type DealStatus = "open" | "won" | "lost";

/** Selling stages plus the closing step, which is what the path renders. */
export const STAGE_STEPS = DEAL_STAGES.length + 1;

/**
 * Drawer pill labels. "Proposal & Quote" is abbreviated because the pill row
 * has to fit the 390px drawer.
 */
export const STAGE_STEP_LABELS: readonly string[] = [
    "Activation",
    "Qualification",
    "Prop. & Quote",
    "Negotiation",
    "Close",
];
