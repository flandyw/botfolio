# Strategy and pattern definitions

Every definition is deterministic and causal. A signal is computed at the **close** of bar *t* using only
bars ≤ *t*, and it is executed at the **open of the next 1-minute bar**. "ATR" is Wilder's ATR(14) on the
strategy's own timeframe. Strategies run independently on 1-minute and 5-minute bars. 5-minute bars are
built from 1-minute bars, anchored to the 09:30 ET open, and emitted only once complete.

Source: `src/ttbot/patterns/*` and `src/ttbot/strategies/*`. Parameters live in `config/default.yaml`.

## Building blocks

### Swing points (fractal pivots), `patterns/swings.py`
Bar *i* is a **swing high** if `high[i] > max(high[i-L .. i-1])` and `high[i] >= max(high[i+1 .. i+R])`.
Swing lows mirror this. The pivot is **confirmed only at the close of bar i+R** and never changes. The rule
is strict on the left and non-strict on the right, so ties go to the first bar.
Defaults: L = R = 3 (structure, order blocks, RSI divergence), L = R = 5 (liquidity).

### Market structure: BOS / CHoCH, `patterns/structure.py`
The tracker keeps the latest confirmed, unbroken swing high and swing low.
* A **bullish break** is the first bar whose **close** is above the active swing high (bearish mirrors).
  Breaks are close-based; wicks don't count.
* **CHoCH** is a break against the current trend (bullish break while the trend is bearish). Otherwise it is a
  **BOS**; the first break of the series is a BOS. Each swing level can be broken only once.

## Pattern strategies

| name | entry rule (long; short mirrors) | structural stop |
|---|---|---|
| **fvg** (Fair Value Gap) | 3 consecutive same-session candles A, B, C with `low[C] > high[A]` form a gap `[high[A], low[C]]`. Requires a gap ≥ 0.25 ATR and a bullish displacement body on B ≥ 0.5 ATR. Enter on the first **later** bar (within 30 bars, same session) whose low trades into the gap (`low ≤ top`) while its close holds (`close ≥ bottom`). A close below the gap invalidates it. One trade per gap. | gap bottom |
| **sweep** (liquidity sweep) | Liquidity = confirmed swing lows (≤ 5 kept, ≤ 120 bars old). A bar whose **low** pierces a swing low but whose **close** is back above it → long. A close below the level is a real break: the level is removed with no trade. | sweep bar's low |
| **ob** (order block) | On a bullish structure break of swing S at bar k: the leg origin is the lowest-low bar in (S, k]. The OB is the **last bearish candle** at or before the origin (≤ 10 bars back), with zone `[low, high]`. Enter on the first later retest (`low ≤ top`, `close > bottom`) within 60 bars, same session. A close below the OB invalidates it. | OB bottom |
| **bos** | Trade in the break direction at the close of a **BOS** bar | latest opposite swing |
| **choch** | Trade in the break direction at the close of a **CHoCH** bar (trend reversal) | latest opposite swing |
| **ema_9_21** | EMA(9) crosses above EMA(21) on a close: `sign(fast-slow)` goes from ≤ 0 to > 0. EMAs are seeded with an SMA. | none (1.5 ATR) |
| **rsi_div** | Two consecutive confirmed swing lows P1, P2, 5–60 bars apart, with price `P2 < P1`, `RSI(P2) > RSI(P1)` and `RSI(P2) ≤ 40` (Wilder RSI 14 read at the pivot bars). Fires at P2's confirmation bar. | P2 low |
| **orb15** | The opening range is the high/low of bars starting in the first 15 minutes. The first later bar that **closes** above the high → long (below the low → short). One trade per session. | opposite side of the range |

FVG and order-block zones are cleared at each new session, so overnight gaps never count as imbalances.

## Intraday momentum (noise area)

`noise_area` (`strategies/momentum.py`) implements Zarattini, Aziz & Barbon (2024) on 1-minute bars. It is not a
TikTok pattern; it is included as a contrast.

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

## Exits and risk (identical for every pattern)

* Stop distance = |signal close − structural stop|, clamped to [0.5, 3] × ATR, or 1.5 × ATR if the pattern
  has no structural stop. The stop is placed that distance from the actual fill price.
* Target = 2 × stop distance. Time stop = 60 bars of the strategy's timeframe. Flat at the session close.
* Sizing: 0.5% of equity at risk, capped at 100% notional. ≤ 2 open positions, ≤ 10 trades/day, and no new
  trades after a −2% day. One position per symbol; signals arriving while in a position are ignored.

## Controls

* **inv_*** – the same signal with the side flipped. The stop is placed at the original target distance
  and the target at the original stop distance, so before costs each trade's return is the exact negative
  of the original's.
* **random_*** – enters on any bar with probability `2 / bars-per-session` and a coin-flip side, with ATR
  stops and the same exits. Seeded per (seed, strategy, symbol) for reproducibility.
* **buy_hold_QQQ / buy_hold_SPY** – buys at the first bar and holds, with no exits or costs beyond the
  entry.
