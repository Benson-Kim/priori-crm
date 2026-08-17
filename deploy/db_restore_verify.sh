#!/usr/bin/env bash
# Restore a production dump into a DISPOSABLE Postgres and verify it.
#
# Used by the monthly scheduled restore test (scheduled:db-restore-verify in
# .gitlab/ci/scheduled-jobs.yml) and runnable by hand during a DR drill or an
# incident. See docs/runbooks/database-backup-restore.md.
#
# A backup that has never been restored is a hope, not a backup. This script
# proves that a dump actually restores and contains recent, plausible,
# internally consistent data:
#   1. migration stamp present (optionally pinned via EXPECTED_ALEMBIC_REV)
#   2. key tables non-empty
#   3. newest audit_events row inside the RPO window
#   4. referential spot-checks (FK orphans, payments-sum consistency)
#   5. index/constraint validity (ANALYZE + one index-backed query plan)
# Each check is reported independently; a summary table precedes the single
# final pass/fail.
#
# Usage:
#   SCRATCH_DB_URL='postgresql://user@host:5432/db' PGPASSWORD='...' \
#     db_restore_verify.sh <dump-file>
#
# Environment:
#   SCRATCH_DB_URL        (required) a DISPOSABLE, EMPTY Postgres database.
#                         Never point this at production — the restore writes
#                         the full schema and data into it. Keep the password
#                         out of the URL (argv is visible in `ps`); pass it
#                         via PGPASSWORD or PGPASSFILE instead.
#   RPO_HOURS             freshness budget for the newest audit_events row
#                         (default 26: nightly cadence plus scheduling slack).
#   REQUIRED_NONEMPTY     space-separated tables that must have > 0 rows.
#   EXPECTED_ALEMBIC_REV  optional; when set, the restored alembic_version
#                         must equal it exactly.

set -euo pipefail

DUMP_FILE="${1:?usage: db_restore_verify.sh <dump-file>}"
: "${SCRATCH_DB_URL:?SCRATCH_DB_URL must point at a disposable Postgres}"
RPO_HOURS="${RPO_HOURS:-26}"
REQUIRED_NONEMPTY="${REQUIRED_NONEMPTY:-users customers invoices payments audit_events}"
EXPECTED_ALEMBIC_REV="${EXPECTED_ALEMBIC_REV:-}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
q() { psql "$SCRATCH_DB_URL" -X -A -t -v ON_ERROR_STOP=1 -c "$1"; }

# Verification summary: every check registers its outcome here so the run
# ends with one table, whatever mix of passes and failures preceded it.
SUMMARY=""
note_result() { SUMMARY="${SUMMARY}$(printf '%-46s %s' "$1" "$2")"$'\n'; }

command -v pg_restore >/dev/null || { log "FATAL: pg_restore not found"; exit 1; }
command -v psql >/dev/null || { log "FATAL: psql not found"; exit 1; }
[ -s "$DUMP_FILE" ] || { log "FATAL: $DUMP_FILE is missing or empty"; exit 1; }

# Refuse to restore into a database that already has tables — a mixed-up URL
# must fail HERE, before pg_restore writes anything.
existing="$(q "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
if [ "$existing" != "0" ]; then
  log "FATAL: target database already has $existing tables in schema public — refusing."
  log "       SCRATCH_DB_URL must be a fresh, disposable database."
  exit 1
fi

log "Restoring $DUMP_FILE ($(wc -c < "$DUMP_FILE") bytes)"
# --no-owner/--no-privileges: the scratch role differs from production's.
# --exit-on-error: a partial restore must be a red run, not a warning.
pg_restore --no-owner --no-privileges --exit-on-error -d "$SCRATCH_DB_URL" "$DUMP_FILE"
log "Restore completed"

fail=0

# --- 1. migration stamp -------------------------------------------------------
rev="$(q "SELECT version_num FROM alembic_version")" || rev=""
if [ -z "$rev" ]; then
  log "FAIL: alembic_version is missing or empty — the dump has no migration stamp"
  note_result "migration stamp" "FAIL (missing)"
  fail=1
else
  log "OK: alembic revision $rev"
  if [ -n "$EXPECTED_ALEMBIC_REV" ] && [ "$rev" != "$EXPECTED_ALEMBIC_REV" ]; then
    log "FAIL: expected alembic revision $EXPECTED_ALEMBIC_REV, dump has $rev"
    note_result "migration stamp" "FAIL ($rev != $EXPECTED_ALEMBIC_REV)"
    fail=1
  else
    note_result "migration stamp" "PASS ($rev)"
  fi
fi

# --- 2. plausible row counts -------------------------------------------------------
for t in $REQUIRED_NONEMPTY; do
  if ! n="$(q "SELECT count(*) FROM \"$t\"")"; then
    log "FAIL: table $t is missing from the restored dump"
    note_result "rows: $t" "FAIL (table missing)"
    fail=1
    continue
  fi
  if [ "$n" = "0" ]; then
    log "FAIL: table $t restored empty"
    note_result "rows: $t" "FAIL (empty)"
    fail=1
  else
    log "OK: $t has $n rows"
    note_result "rows: $t" "PASS ($n rows)"
  fi
done

# --- 3. freshness: the newest audit row must be inside the RPO window ---------
# Every financially significant mutation writes an audit_events row in the
# same transaction (docs/database.md §4), so its max(created_at) is a good
# proxy for "how recent is this backup".
age_hours="$(q "SELECT COALESCE(floor(extract(epoch FROM (now() - max(created_at))) / 3600)::text, '') FROM audit_events")" || age_hours=""
if [ -z "$age_hours" ]; then
  log "FAIL: could not compute audit_events freshness (table missing or empty)"
  note_result "audit_events freshness" "FAIL (missing/empty)"
  fail=1
elif [ "$age_hours" -gt "$RPO_HOURS" ]; then
  log "FAIL: newest audit_events row is ${age_hours}h old — outside the ${RPO_HOURS}h RPO window."
  log "      Either the nightly backup is not running, or the offsite sync is stale."
  note_result "audit_events freshness" "FAIL (${age_hours}h > ${RPO_HOURS}h)"
  fail=1
else
  log "OK: newest audit_events row is ${age_hours}h old (budget ${RPO_HOURS}h)"
  note_result "audit_events freshness" "PASS (${age_hours}h <= ${RPO_HOURS}h)"
fi

# --- 4. referential spot-checks -------------------------------------------------
# A dump can restore cleanly and still be internally inconsistent (partial
# writes would violate docs/database.md's invariants; a corrupted dump can
# too). Spot-check the invariants a financial ledger cannot live without.

# 4a. every invoices.customer_id resolves to a customer.
orphans="$(q "SELECT count(*) FROM invoices i LEFT JOIN customers c ON c.id = i.customer_id WHERE c.id IS NULL")" || orphans=""
if [ "$orphans" = "0" ]; then
  log "OK: all invoices.customer_id resolve to a customer"
  note_result "referential: invoices -> customers" "PASS"
else
  log "FAIL: ${orphans:-?} invoice(s) reference a missing customer"
  note_result "referential: invoices -> customers" "FAIL (${orphans:-unknown} orphans)"
  fail=1
fi

# 4b. every payments.invoice_id resolves to an invoice.
orphans="$(q "SELECT count(*) FROM payments p LEFT JOIN invoices i ON i.id = p.invoice_id WHERE i.id IS NULL")" || orphans=""
if [ "$orphans" = "0" ]; then
  log "OK: all payments.invoice_id resolve to an invoice"
  note_result "referential: payments -> invoices" "PASS"
else
  log "FAIL: ${orphans:-?} payment(s) reference a missing invoice"
  note_result "referential: payments -> invoices" "FAIL (${orphans:-unknown} orphans)"
  fail=1
fi

# 4c. payments-sum consistency on a sample of invoices that have payments:
# amount_paid must equal SUM(payments.amount) exactly (docs/database.md §6 —
# this holds even for overpayments, which only drive balance_due negative).
# Sample is deterministic (first 50 invoice ids with payments) so failures
# are reproducible across reruns.
SAMPLE_SQL="SELECT DISTINCT invoice_id FROM payments ORDER BY invoice_id LIMIT 50"
sampled="$(q "SELECT count(*) FROM ($SAMPLE_SQL) s")" || sampled=""
mismatched="$(q "SELECT count(*) FROM (
    SELECT i.id
    FROM invoices i
    JOIN payments p ON p.invoice_id = i.id
    WHERE i.id IN ($SAMPLE_SQL)
    GROUP BY i.id, i.amount_paid
    HAVING i.amount_paid <> SUM(p.amount)
  ) mismatched")" || mismatched=""
if [ -z "$sampled" ] || [ -z "$mismatched" ]; then
  log "FAIL: could not run the payments-sum consistency check"
  note_result "payments sum = amount_paid" "FAIL (query error)"
  fail=1
elif [ "$sampled" = "0" ]; then
  # No invoice has any payment — plausible only on a very young database;
  # surface it rather than silently passing.
  log "WARNING: no invoices with payments to sample — payments-sum check skipped"
  note_result "payments sum = amount_paid" "SKIPPED (no paid invoices)"
elif [ "$mismatched" != "0" ]; then
  log "FAIL: $mismatched of $sampled sampled invoice(s) have amount_paid <> SUM(payments.amount)"
  note_result "payments sum = amount_paid" "FAIL ($mismatched of $sampled)"
  fail=1
else
  log "OK: amount_paid = SUM(payments.amount) on all $sampled sampled invoices"
  note_result "payments sum = amount_paid" "PASS ($sampled sampled)"
fi

# --- 5. index/constraint validity ----------------------------------------------
# ANALYZE proves every table/index is readable end-to-end and refreshes
# statistics; the EXPLAIN proves at least one unique index survived the
# restore and is actually usable by the planner (enable_seqscan=off makes a
# missing index visible as a Seq Scan in the plan).
if q "ANALYZE" >/dev/null; then
  log "OK: ANALYZE completed over the restored database"
  note_result "ANALYZE" "PASS"
else
  log "FAIL: ANALYZE failed — a relation or index is unreadable"
  note_result "ANALYZE" "FAIL"
  fail=1
fi

plan="$(q "SET enable_seqscan = off; EXPLAIN SELECT id FROM users WHERE email = (SELECT min(email) FROM users)")" || plan=""
if printf '%s' "$plan" | grep -q "Index"; then
  log "OK: indexed lookup on users.email uses an index scan"
  note_result "indexed query (users.email)" "PASS"
else
  log "FAIL: indexed lookup on users.email did not use an index — index missing or invalid"
  [ -n "$plan" ] && log "      plan: $(printf '%s' "$plan" | head -n 3 | tr '\n' ' ')"
  note_result "indexed query (users.email)" "FAIL"
  fail=1
fi

# --- verification summary --------------------------------------------------------
log "==== verification summary ========================="
printf '%s' "$SUMMARY"
log "===================================================="

if [ "$fail" -ne 0 ]; then
  log "RESTORE VERIFICATION FAILED"
  exit 1
fi
log "RESTORE VERIFICATION PASSED"
