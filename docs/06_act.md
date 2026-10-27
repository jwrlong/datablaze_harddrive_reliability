# Phase 6: Act

## Purpose

Turn the measurements into a decision she can approve or reject, and name the data that would change it.

## What this phase produces

The action list. The same list is the recommendation section of the memo.

## The work

1. Approve WDC `WUH722222ALE6L4` for 80% of new terabytes and Toshiba `MG11ACA24TE` for 20%.
2. Stock 7 spares of the WDC model and 3 of the Toshiba for a 42-day lead time at 99% service on the 100 PB plan. Recompute the Poisson quantile if the fleet or the lead time changes. Do not scale the spare count by dividing.
3. Stop new purchases of the 13 models in Phase 4. Do not buy Seagate `ST24000NM002H` as a 24 TB substitute.
4. Put drives at or above 4.0 power-on years on the replacement list. Use SMART 9 age. The date a drive first appears in a report is not its age. Expect the hazard to keep climbing after 4 years, toward about 7% per year by 8.5.
5. Treat Q2 2026's fleet AFR of 1.75% as a reason to recompute next quarter, not as a reason to abandon the model rates. The standard model's own quarter was 0.55%.

**Limits.** These are Backblaze data-center drives under Backblaze's workload and replacement practice. The scenario is a civilian analog of a sustainment problem. It is not an aircraft or military parts list, and the rates may not travel. Failure is their removal flag. A drive that vanishes without `failure = 1` is censored. Model gaps are associations: maker hazard ratios point the same way as the AFRs and still mix model with building. The proportional-hazards check on follow-up time failed for HGST, which is another reason those ratios are not the buy rule. The mission, the lead time, the service level, the spare budget, and the prices are assumptions. The prices are not quotes. The spare budget does not bind at these failure rates, so the dollar schedule is not what chose the model. The sample-size rule is.

**What would change the recommendation.** Pod-level assignment of model, workload, and temperature, so a model gap can be separated from a building. A failure flag that distinguishes migration from death. The same table run on the next published quarter, especially if Q2's fleet rate persists. A real quote and a real lead time, which change the spare integer and do not, by themselves, change the stop list.

## Check

The recommendation can be accepted or rejected as four actions: a standard, a second source, a spare count, and a stop list. Each one has a number behind it, and each limit says what evidence would replace it.
