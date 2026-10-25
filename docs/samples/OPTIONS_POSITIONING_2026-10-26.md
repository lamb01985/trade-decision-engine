# Options Positioning Sample — October 26, 2026

Generated October 25 from Massive observations through
`2026-10-23T20:15:00.253190+00:00`. The complete reproducible report is
`data/market_intelligence_reports/market_map_2026-10-26.json`.

## QQQ

- 670 contracts across October 26–28; no missing OI.
- October 26 call/put OI: 67,245 / 94,769.
- Largest observed concentrations: 735 call 19,613; 680 put 17,404;
  722 put 15,073; 695 put 13,633; 740 call 13,428.
- Three contracts passed the five-observation volume-baseline rule. These are
  elevated-volume observations, not evidence of institutions or opening trades.
- The bounded `O:QQQ261026C00710000` flow sample contained 250 trades:
  24 buyer-initiated estimates, 13 seller-initiated estimates and 213 ambiguous.
  Both trade and quote pagination were incomplete, so this is not whole-session flow.
- ATM straddle-midpoint expected moves were 9.715, 13.06 and 16.55 for the
  October 26, 27 and 28 expirations.
- IV rank and percentile were withheld because no prior daily IV snapshots existed.

## SPY

- 682 contracts across October 26–28; no missing OI.
- October 26 call/put OI: 102,307 / 292,006.
- Largest observed concentrations: 750 put 94,515; 755 put 87,016;
  767 put 33,769; 741 put 22,868; 766 put 19,362.
- Three contracts passed the five-observation volume-baseline rule, subject to the
  same non-directional limitation.
- The bounded `O:SPY261026P00760000` flow sample contained 250 trades:
  32 buyer-initiated estimates, 55 seller-initiated estimates and 163 ambiguous.
  Both trade and quote pagination were incomplete.
- ATM straddle-midpoint expected moves were 6.075, 8.07 and 10.595 for the
  October 26, 27 and 28 expirations.
- IV rank and percentile were withheld because no prior daily IV snapshots existed.

Gamma totals and concentrations in the JSON are scenarios, not measured dealer
positions or verified support/resistance. OI is prior-session inventory and cannot
identify direction, owner, or opening/closing intent. Estimated notional uses session
volume times the latest midpoint; it is not actual executed premium.
