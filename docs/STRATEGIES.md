# Strategy definitions

Every definition is deterministic and causal. A signal is computed at the **close** of bar *t* using only
bars ≤ *t*, and it is executed at the **open of the next 1-minute bar**. "ATR" is Wilder's ATR(14) on the
strategy's own timeframe. Strategies run independently on 1-minute and 5-minute bars. 5-minute bars are
built from 1-minute bars, anchored to the 09:30 ET open, and emitted only once complete.

Source: `src/botfolio/strategies/*`. Parameters live in `config/default.yaml`.

## Intraday momentum (noise area)

`noise_area` (`strategies/noise_area.py`, portfolios `noise_qqq_1m` / `noise_spy_1m`) is a second, independent
implementation of Zarattini, Aziz & Barbon (2024) on 1-minute bars, alongside `noise_mom` below. It differs in
sizing (full notional, no stop) and exits (flat inside the band, optional VWAP).

* σ(m) = the mean, over the previous 14 sessions, of |close(m) / session open − 1| at minute-of-session *m*.
* Upper band = max(open, previous close) × (1 + σ(m)); lower band = min(open, previous close) × (1 − σ(m)).
* At the close of each bar that ends on a 30-minute boundary (10:00, 10:30, …, 15:30), the desired position is
  long if close > upper band, short if close < lower band, and flat otherwise. A signal is emitted only when this
  changes. Entries are `reverse` signals, so they flip an opposite position at the next open. A move back inside
  the band is an `exit` signal, which closes at the next open.
* `use_vwap: true` uses max(upper, VWAP) / min(lower, VWAP), as in the paper. It is off by default because forward
  trading uses IEX volume.
* The strategy has its own exit and risk overrides in the YAML: `stop_mode: none` (no stop or target),
  `max_hold_bars: 390`, flat at the close, `sizing: notional` at 100% of equity, and ≤ 20 trades/day. It needs
  15 prior sessions, which the forward runner replays at start-up.

## Exits and risk (shared defaults)

* Stop distance = |signal close − structural stop|, clamped to [0.5, 3] × ATR, or 1.5 × ATR if the strategy
  has no structural stop. The stop is placed that distance from the actual fill price.
* Target = 2 × stop distance. Time stop = 60 bars of the strategy's timeframe. Flat at the session close.
* Sizing: 0.5% of equity at risk, capped at 100% notional. ≤ 2 open positions, ≤ 10 trades/day, and no new
  trades after a −2% day. One position per symbol; signals arriving while in a position are ignored.

## Holding strategy: ETF trend filter, `strategies/trend.py`

**trend_SPY / trend_QQQ** hold one ETF each (one $100k portfolio per symbol, so each compares directly with
`buy_hold_<symbol>`). It follows the 200-day / 10-month moving-average rule (Siegel; Faber 2007).

* At the first bar of each session, take the previous session's close and the simple average of the last
  `sma_days` (200) daily closes.
* Own the ETF when the close is above `SMA × (1 + band)`, sit in cash below `SMA × (1 − band)`, and keep
  the current state in between (`band_pct` 1%). The band cuts whipsaw round trips.
* Until `sma_days` closes exist it stays invested, i.e. it is identical to buy-and-hold rather than
  sitting in cash. Live trading replays `sma_days + 5` sessions at a fresh start so it is ready at once.
* Fills at the next minute's open with the normal spread, slippage and sell-side fees. Positions are
  `hold`: no stop, target, time stop or end-of-day flatten, so they collect dividends. No shorting, no
  inverse twin.
* Expected behaviour: it rarely beats buy-and-hold on return. Its purpose is to avoid most of a long
  bear market. Each switch costs spread and slippage, and the time in cash forgoes dividends.

## Dividends (all strategies)

Prices are split-adjusted only. On each ex-date, a long held through the previous close receives the cash
dividend minus `costs.dividend_withholding_pct`; a short pays it in full. Dividends count in the trade's
gross and net P&L and in portfolio equity. Intraday strategies are flat overnight, so they never collect any.

## Managed strategy: intraday noise-band momentum, `strategies/momentum.py`

**noise_mom** follows Zarattini, Aziz & Barbon (2024), *Beat the Market: An Effective Intraday Momentum
Strategy for S&P500 ETF (SPY)*. It has a published out-of-sample record.

* At each 30-minute mark, `sigma` = mean of `|close / session open − 1|` at the same time of day over the
  previous 14 sessions (today excluded). Noise area =
  `[min(open, prev close) × (1 − sigma), max(open, prev close) × (1 + sigma)]`.
* **Long** when the close is above the noise area and above session VWAP. **Short** mirrors.
* **Exit** at the first 30-minute mark whose close is back inside `max(upper band, VWAP)` (long), otherwise
  at the session close. No profit target and no time stop: the edge is in the few days that trend all day.
* Hard stop = the far side of the noise area. It also sets the size (0.5% of equity at risk, ≤ 100% notional).
* Its signals are `managed`: the shared 2R target / 60-bar time stop / ATR stop clamp do not apply. All other
  risk limits and costs do.

## Controls

* **inv_*** – the same signal with the side flipped. The stop is placed at the original target distance
  and the target at the original stop distance, so before costs each trade's return is the exact negative
  of the original's.
* **random_*** – enters on any bar with probability `2 / bars-per-session` and a coin-flip side, with ATR
  stops and the same exits. Seeded per (seed, strategy, symbol) for reproducibility.
* **buy_hold_QQQ / buy_hold_SPY** – buys at the first bar and holds, with no exits or costs beyond the
  entry.
