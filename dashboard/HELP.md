### How to interpret the results

**The question:** do popular TikTok / ICT / SMC chart patterns predict intraday QQQ/SPY moves well enough to
make money *after costs*, compared with random entries?

* **avg net %/trade** – mean P&L per trade as % of position notional, after spread, slippage and fees.
  Compare **gross** vs **cost**: on 1-minute charts the costs (~0.02–0.04% per round trip) are often larger
  than any signal.
* **Inverse strategies** take the exact opposite trade with mirrored stop/target. If a pattern *and* its
  inverse both lose about the cost of trading, the pattern has no information.
* **Random baseline** enters at random times, coin-flip direction, same exits. This is what "no skill" looks
  like in this exact simulator.
* **p (t-test)** – probability of an average this good if the true mean per-trade return were 0.
* **p (vs random)** – Monte-Carlo: fraction of 2,000 random-entry portfolios (same number of trades, same
  long/short mix, same stop size in ATRs, same reward/risk, same costs) that did at least as well.
* **q** – the same p-values after Benjamini–Hochberg correction for testing ~30 strategies at once. With 30
  tests, one or two raw p < 0.05 are *expected by chance*. Only **q < 0.05** counts.
* **Verdict = "significant edge"** requires ≥ 30 trades and q < 0.05 on both tests.
* **Sharpe** is annualised from daily equity changes (risk-free rate 0). **Max DD** uses daily closes and
  trade exits. **Profit factor** = gross wins / gross losses (net of costs).

**Caveats:** a single backtest period is one sample of market history; patterns that pass should be
re-tested on an untouched later period (forward mode) before being believed. Free IEX data covers only a
few % of volume, so 1-minute IEX candles are noisier than the consolidated tape.
