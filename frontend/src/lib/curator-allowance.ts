/** The monthly curation allowance, stated once for both surfaces that show it.
 *
 *  The Curators rows in settings and the developer console's curator page both
 *  read the same two numbers — the row's worked-run count and the account's
 *  ceiling — so the sentence and the rule for when it applies live here once
 *  rather than as two phrasings of one figure that can drift apart.
 *
 *  The ceiling is the server's value, and `null` is its answer for an account
 *  the allowance does not bound at all (Pro, internal, and enterprise — see
 *  `billing_service.is_pro`). That is why this returns null rather than working
 *  from a defaulted number: a `?? 0` would state the opposite of what the server
 *  said, and a `?? 10` would state a ceiling that config owns.
 *
 *  A zero counter with no credits outcome has consumed nothing worth stating, so
 *  it gets no line. Zero is NOT a finding that the lane is unmetered — that
 *  verdict is the server's alone, and this file must not re-derive it from
 *  `model_provider` or a credential id. The counter is the server's record of
 *  metered runs that worked this calendar month; before STAS-412 a tick that
 *  dispatched nothing was counted as though it had run, and migration 0273 clamps
 *  a row past the allowance back to the run stamps the month actually holds.
 *  A row the clamp left alone can therefore still print a pre-fix count against
 *  the ceiling, which is the server's own number, stated rather than inferred.
 */
export function curatorAllowanceLine(
  curator: { month_run_count: number; last_run_outcome: string | null },
  freeRunsPerMonth: number | null,
): string | null {
  if (freeRunsPerMonth === null) return null;
  if (curator.month_run_count === 0 && curator.last_run_outcome !== "skipped_credits") return null;
  return `${curator.month_run_count} of ${freeRunsPerMonth} free runs used this month.`;
}
