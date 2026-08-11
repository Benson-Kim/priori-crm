/**
 * Pipeline stage constants — kept separate from StagePath.tsx (like
 * button-variants.ts) so the component file only exports components
 * (eslint react-refresh/only-export-components). Pages import these to
 * build stage filters, columns and captions.
 */

/** The 5 pipeline stages in fixed order (style-reference.md §5). */
export const PIPELINE_STAGES = [
  "Activation",
  "Qualification",
  "Proposal & Quote",
  "Negotiation",
  "Won",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export type DealStatus = "open" | "won" | "lost";
