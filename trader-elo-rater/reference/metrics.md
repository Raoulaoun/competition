# Metric Reference

Every metric is recomputed from the MT5 **Deals** section. Realized trades are
`Direction == out` deals; net per trade = Profit + Swap + Commission + Fee.
The equity curve is the `Balance` column after each event.

| Metric | Definition | Pillar | Anti-gaming role |
|---|---|---|---|
| `net_return_pct` | (end − start) / start balance | Return/alpha | Light-weighted; raw return is the easiest metric to game with size |
| `sharpe` | mean(trade net) / std(trade net), per-trade | Risk-adjusted | Reward per unit of total variability |
| `sortino` | mean(trade net) / downside deviation | Risk-adjusted | Punishes downside only; upside swings not penalized |
| `return_over_maxdd` | net_return_pct / \|max_drawdown\| | Risk-adjusted | Return earned per unit of worst pain |
| `max_drawdown` | min peak-to-trough on equity curve (negative) | Preservation | The core preservation signal |
| `ulcer_index` | sqrt(mean(negative drawdown²)) | Preservation | Penalizes depth AND duration, not just the single worst point |
| `recovery_factor` | net P/L / \|max drawdown $\| | Preservation | Did they dig out of holes or just sit in them |
| `profit_factor` | gross profit / \|gross loss\| | Payoff | < 1 means losing; immune to win-rate gaming |
| `payoff_ratio` | avg win / \|avg loss\| | Payoff | Catches small-wins / huge-losses skew |
| `expectancy` | mean net per trade | Payoff | Expected $ per trade regardless of win rate |
| `largest_loss_pct` | worst single trade / start balance | Payoff | Direct blow-up-proximity gauge |
| `win_rate` | wins / trades | (context only) | NEVER weighted alone — most gameable metric |
| `equity_linearity` | R² of balance vs trade index, signed | Consistency | Smooth steady climbers score high; signed so downward-linear ≠ rewarded |
| `trade_return_std` | std of per-trade net | Consistency | Erratic results penalized |
| `peak_leverage` | max(volume × 100,000 × price / balance) | Hygiene | Catches reckless sizing even when it paid off (805× in the sample blowup) |
| `leverage_consistency` | std of the leverage proxy | Hygiene | Lower = disciplined repeatable sizing |
| `trade_count` | number of closed trades | Gate | Too few = one lucky bet; gate, not score |
| `blowup_flag` | maxDD ≤ −50% or end ≤ 10% of start | Gate | Disqualification trigger |

## Leverage proxy note
`volume × 100,000 × price` is a *consistent relative* notional (FX standard lot =
100,000 base units), not an exact USD margin figure. It is valid for ranking risk
WITHIN a cohort, which is all the rating needs. Exact margin would require per-pair
contract specs and account leverage settings.

## Z-scoring and sign orientation
Within the eligible cohort, each metric is converted to a z-score and clipped to
±3 to stop a single outlier from dominating. Metrics where lower is better
(`ulcer_index`, `trade_return_std`, `peak_leverage`, `leverage_consistency`) carry
`direction: -1` in weights.json and are negated before weighting. `max_drawdown`
and `largest_loss_pct` are stored negative, so `direction: 1` already orients them
correctly (closer to zero = better).
