/**
 * Jurisdiction-aware Compliance Ref configuration and create-time Purchase
 * Order defaults (PO-10 / PO-11).
 *
 * Single source of truth (DRY): the Compliance Ref label/tooltip and the PO
 * defaults live here only, so the Create/Edit form, the View screen and the
 * PDF all resolve the same values. New jurisdictions are added by extending
 * the map (open/closed); an unknown jurisdiction falls back to the generic
 * entry.
 *
 * The jurisdiction is org-scoped Settings data, now persisted on the owner
 * profile (PO-11). DEFAULT_ORG_JURISDICTION / DEFAULT_PURCHASE_ORDER_TERMS
 * below are the built-in fallbacks used only when the org never configured a
 * value; resolveOrgJurisdiction / resolveDefaultTerms prefer the persisted
 * Settings value and fall back to these constants.
 */

export interface ComplianceRefConfig {
  /** Field label shown on the form, View and PDF. */
  label: string;
  /** Helper tooltip explaining the reference for the jurisdiction. */
  tooltip: string;
}

/** Generic fallback for any jurisdiction without a specific entry. */
export const GENERIC_COMPLIANCE_REF: ComplianceRefConfig = {
  label: "Compliance Ref",
  tooltip:
    "Optional tax or regulatory reference for this purchase order. Free text.",
};

/**
 * Jurisdiction code -> Compliance Ref label/tooltip. Codes match
 * COUNTRY_OPTIONS in lib/constants (ISO 3166-1 alpha-2). Extend this map to
 * add a jurisdiction; do not branch per form.
 */
export const COMPLIANCE_REF_BY_JURISDICTION: Record<string, ComplianceRefConfig> = {
  KE: {
    label: "ETIMS Ref",
    tooltip:
      "KRA eTIMS-compliant invoice reference for this purchase order. Optional, free text.",
  },
};

/**
 * The org's configured jurisdiction (Settings-driven). Centralised so it can
 * be swapped for a Settings lookup later without touching any form. Defaults
 * to Kenya, matching the default currency (KES).
 */
export const DEFAULT_ORG_JURISDICTION = "KE";

/** Out-of-the-box Terms & Conditions prefilled on new POs (PO-11). */
export const DEFAULT_PURCHASE_ORDER_TERMS =
  "Upon accepting this purchase order, you hereby agree to the terms & conditions.";

/** Resolve the Compliance Ref config for a jurisdiction (generic fallback). */
export function getComplianceRefConfig(
  jurisdiction: string = DEFAULT_ORG_JURISDICTION
): ComplianceRefConfig {
  return COMPLIANCE_REF_BY_JURISDICTION[jurisdiction] ?? GENERIC_COMPLIANCE_REF;
}

export function getComplianceRefLabel(
  jurisdiction?: string
): string {
  return getComplianceRefConfig(jurisdiction).label;
}

export function getComplianceRefTooltip(
  jurisdiction?: string
): string {
  return getComplianceRefConfig(jurisdiction).tooltip;
}

/**
 * Resolve the org jurisdiction from the persisted Settings value, falling
 * back to the built-in default when the org never configured one. The owner
 * profile endpoint already returns a resolved (non-null) value, but the
 * fallback keeps callers safe before the profile has loaded.
 */
export function resolveOrgJurisdiction(
  jurisdiction?: string | null
): string {
  const trimmed = jurisdiction?.trim();
  return trimmed ? trimmed : DEFAULT_ORG_JURISDICTION;
}

/**
 * Resolve the create-time default Terms & Conditions from the persisted
 * Settings value, falling back to the built-in default text when unset.
 */
export function resolveDefaultTerms(
  defaultTermsAndConditions?: string | null
): string {
  const trimmed = defaultTermsAndConditions?.trim();
  return trimmed ? trimmed : DEFAULT_PURCHASE_ORDER_TERMS;
}
