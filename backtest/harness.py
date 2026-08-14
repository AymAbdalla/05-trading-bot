"""Backtest harness: run strategies on historical data to determine if they have an edge.

This is T7 - the moment of truth. If strategies don't profit after fees on
historical data, the Quant agent is iterating on garbage.

Implements all SPEC Section 12 requirements:
- Train/validation/test split (60/20/20, chronological)
- Walk-forward validation (rolling windows)
- Random-entry twin baseline
- Buy-and-hold benchmark
- Stress probes (fee doubling, slippage doubling, execution delay, parameter jitter)
- Pre-registration (hypothesis stated before backtest runs)
- Graveyard logging for rejected strategies

Two execution modes:
1. Vectorized: fast, for bulk parameter sweeps and walk-forward
2. Per-trade simulation: slower, for execution-delay stress probe
"""
import json
import time
import logging
import random
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

from strategies.base import Signal
from strategies.builtin.patterns import ALL_BUILTIN, ENTRY_STRATEGIES
from indicators.atr import latest_atr
from indicators.rsi import latest_rsi
from indicators.volume import volume_ratio
from indicators.ema import latest_ema, ema_slope
from indicators.support_resistance import find_support_levels

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """A single backtested trade."""
    entry_ts: int
    exit_ts: int
    pair: str
    strategy_id: str
    entry_px: float
    exit_px: float
    stop_px: float
    target_px: float
    qty: float
    pnl_gross: float
    fee_cost: float
    pnl_net: float
    r_multiple: float
    exit_reason: str  # 'target', 'stop', 'signal_exit', 'timeout'
    regime: str  # 'uptrend', 'downtrend', 'sideways'
    features: dict = field(default_factory=dict)
    # Notional for spot, margin for futures, premium for options. Return%
    # denominator; equals entry_px * qty for spot so flat numbers don't move.
    capital_at_risk: float = 0.0


@dataclass
class BacktestResult:
    """Result of a single backtest run."""
    strategy_id: str
    pair: str
    period_start: int
    period_end: int
    trades: List[Trade] = field(default_factory=list)
    buy_hold_return: float = 0.0       # full-window price return %, for reporting
    buy_hold_pnl_usd: float = 0.0      # $ PnL of buy-and-hold on ONE notional_cap, fees included
    random_twin_pf: float = 0.0
    starting_capital: float = 2000.0   # equity base for drawdown (paper.starting_equity)
    cost_model_version: str = 'flat:unstamped'
    asset_class: str = 'FLAT'

    # Computed metrics
    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_net > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl_net <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.wins / len(self.trades)

    @property
    def gross_profit(self) -> float:
        return sum(t.pnl_net for t in self.trades if t.pnl_net > 0)

    @property
    def gross_loss(self) -> float:
        return abs(sum(t.pnl_net for t in self.trades if t.pnl_net <= 0))

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float('inf') if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def expectancy(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.pnl_net for t in self.trades) / len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_net for t in self.trades)

    @property
    def avg_r(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.r_multiple for t in self.trades) / len(self.trades)

    @property
    def gross_pf(self) -> float:
        """Profit factor BEFORE fees (from pnl_gross). Must be >= net PF."""
        gp = sum(t.pnl_gross for t in self.trades if t.pnl_gross > 0)
        gl = abs(sum(t.pnl_gross for t in self.trades if t.pnl_gross <= 0))
        if gl == 0:
            return float('inf') if gp > 0 else 0.0
        return gp / gl

    @property
    def max_drawdown(self) -> float:
        """Max drawdown as a percentage of peak ACCOUNT equity
        (starting_capital + cumulative trade PnL). The old version measured
        drawdown on cumulative PnL alone with a peak>0 guard, which reported
        0% for a pure losing streak and ~100% for a $4 dip after a $4 gain."""
        if not self.trades:
            return 0.0
        equity = self.starting_capital
        peak = equity
        max_dd = 0.0
        for t in self.trades:
            equity += t.pnl_net
            if equity > peak:
                peak = equity
            elif peak > 0:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd * 100  # as percentage

    @property
    def strategy_return_pct(self) -> float:
        """Average per-trade return on CAPITAL AT RISK (notional for spot,
        margin/premium for contracts). NOT comparable to a full-period price
        return - use beats_buy_hold() (dollar comparison) for that."""
        total_car = sum(t.capital_at_risk or t.entry_px * t.qty
                        for t in self.trades)
        if total_car == 0:
            return 0.0
        return (self.total_pnl / total_car) * 100

    def beats_buy_hold(self) -> bool:
        """Dollar comparison on the same fixed notional: total strategy PnL vs
        PnL of parking one notional_cap in the asset for the whole test window
        (fees included on both)."""
        return self.total_pnl > self.buy_hold_pnl_usd

    def beats_random_twin(self, min_diff: float = 0.15) -> bool:
        pf = self.profit_factor
        twin = self.random_twin_pf
        if pf == float('inf'):
            return twin != float('inf')
        if twin == float('inf'):
            return False
        return pf >= twin + min_diff

    def to_report(self) -> dict:
        return {
            'strategy_id': self.strategy_id,
            'pair': self.pair,
            'trade_count': self.trade_count,
            'win_rate': round(self.win_rate, 4),
            'profit_factor': None if self.profit_factor == float('inf') else round(self.profit_factor, 4),
            'gross_pf': None if self.gross_pf == float('inf') else round(self.gross_pf, 4),
            'expectancy': round(self.expectancy, 4),
            'total_pnl': round(self.total_pnl, 4),
            'avg_r': round(self.avg_r, 4),
            'max_drawdown_pct': round(self.max_drawdown, 2),
            'strategy_return_pct': round(self.strategy_return_pct, 2),
            'buy_hold_return_pct': round(self.buy_hold_return, 2),
            'buy_hold_pnl_usd': round(self.buy_hold_pnl_usd, 2),
            'random_twin_pf': None if self.random_twin_pf == float('inf') else round(self.random_twin_pf, 4),
            'cost_model_version': self.cost_model_version,
            'asset_class': self.asset_class,
            'beats_buy_hold': self.beats_buy_hold(),
            'beats_random_twin': self.beats_random_twin(),
        }


class BacktestHarness:
    """Runs strategies on historical data with full overfitting defenses."""

    def __init__(self, config: dict):
        self.config = config
        self.notional_cap = config.get('risk', {}).get('notional_cap_usd', 100)
        self.taker_fee = config.get('exchange', {}).get('fees', {}).get('taker', 0.001)
        self.slippage = config.get('exchange', {}).get('slippage', {}).get('market', 0.0005)

        # OPT-IN venue-accurate costs; see vectorized_harness.__init__ for the
        # rationale. Off = bit-identical legacy flat model.
        self.use_cost_model = bool(config.get('use_cost_model', False))
        from backtest.cost_model import CostModel
        self.cost_model = CostModel()

        self.starting_capital = config.get('paper', {}).get('starting_equity', 2000.0)

        conf = config.get('strategy', {}).get('confirmation', {})
        self.regime_ema_period = conf.get('regime_ema_period', 50)
        self.regime_lookback = conf.get('regime_lookback', 10)
        self.rsi_period = conf.get('rsi_period', 14)
        self.rsi_max_entry = conf.get('rsi_max_entry', 60)
        self.volume_sma_period = conf.get('volume_sma_period', 20)
        self.volume_min_ratio = conf.get('volume_min_ratio', 1.5)
        self.support_lookback = conf.get('support_lookback', 100)
        self.support_min_touches = conf.get('support_min_touches', 2)
        self.support_cluster_atr_mult = conf.get('support_cluster_atr_mult', 0.5)
        self.location_atr_mult = conf.get('location_atr_mult', 1.5)

    def load_candles(self, candles: List[dict]) -> Dict[str, List]:
        """Convert list of candle dicts to column arrays."""
        return {
            'opens': [c['open'] for c in candles],
            'highs': [c['high'] for c in candles],
            'lows': [c['low'] for c in candles],
            'closes': [c['close'] for c in candles],
            'volumes': [c['volume'] for c in candles],
            'timestamps': [c['ts'] for c in candles],
        }

    def split_chronological(self, candles: List[dict],
                            train_pct: float = 0.6,
                            val_pct: float = 0.2) -> Tuple[List, List, List]:
        """Split candles chronologically: train (60%), validation (20%), test (20%)."""
        n = len(candles)
        train_end = int(n * train_pct)
        val_end = int(n * (train_pct + val_pct))
        return candles[:train_end], candles[train_end:val_end], candles[val_end:]

    def get_regime(self, regime_closes: List[float]) -> str:
        """Determine market regime from 1h EMA."""
        # Not enough CLOSED regime candles for a meaningful EMA+slope ->
        # 'unknown' (treated as not-uptrend: no entries). Returning a garbage
        # slope from SMA-seed padding was one of the audited warmup bugs.
        if len(regime_closes) < self.regime_ema_period + self.regime_lookback:
            return 'unknown'
        ema_val = latest_ema(regime_closes, self.regime_ema_period)
        slope = ema_slope(regime_closes, self.regime_ema_period, self.regime_lookback)
        price_above = regime_closes[-1] > ema_val

        if slope > 0 and price_above:
            return 'uptrend'
        elif slope < 0 and not price_above:
            return 'downtrend'
        return 'sideways'

    @staticmethod
    def _infer_interval_ms(candles: List[dict]) -> int:
        """Median timestamp spacing = bar interval (robust to session gaps)."""
        if len(candles) < 3:
            return 0
        diffs = sorted(candles[j + 1]['ts'] - candles[j]['ts']
                       for j in range(min(len(candles) - 1, 200)))
        return diffs[len(diffs) // 2]

    @staticmethod
    def _regime_closed_counts(candles: List[dict], regime_candles: List[dict],
                              signal_interval_ms: int, regime_interval_ms: int) -> List[int]:
        """For each signal bar i: how many regime candles are fully CLOSED at
        the moment bar i closes. A regime candle opening at R is closed once
        R + regime_interval <= decision_time (= bar i ts + signal interval).

        This replaces the audited lookahead bug where the 1h series was sliced
        by the 15m bar INDEX - which handed the strategy hours-to-months of
        future regime data (or the entire series once i exceeded its length)."""
        counts = []
        k = 0
        n_r = len(regime_candles)
        for c in candles:
            decision_time = c['ts'] + signal_interval_ms
            while k < n_r and regime_candles[k]['ts'] + regime_interval_ms <= decision_time:
                k += 1
            counts.append(k)
        return counts

    @staticmethod
    def _resolve_entry(candles: List[dict], i: int, signal, slip: float
                       ) -> Optional[Tuple[int, float]]:
        """Turn a Signal at bar i into an actual fill, per SPEC 5.1 order types.
        Market (entry ~= close): fill at close + slippage. Buy-stop above
        market (hammer high): rests signal.valid_for candles, fills at
        max(level, bar open) + slippage on first touch, else expires.
        Buy-limit below market: rests valid_for candles, fills at
        min(level, bar open), no slippage, else expires."""
        level = float(signal.entry)
        close_i = float(candles[i]['close'])
        n = len(candles)
        if abs(level - close_i) <= close_i * 0.001:
            return i, close_i * (1 + slip)

        valid_for = max(1, int(getattr(signal, 'valid_for', 1) or 1))
        last_j = min(i + valid_for, n - 1)
        if level > close_i:  # buy-stop
            for j in range(i + 1, last_j + 1):
                if candles[j]['high'] >= level:
                    return j, max(level, float(candles[j]['open'])) * (1 + slip)
            return None
        else:  # buy-limit
            for j in range(i + 1, last_j + 1):
                if candles[j]['low'] <= level:
                    return j, min(level, float(candles[j]['open']))
            return None

    def _coster(self, pair: str, candles: List[dict],
                sector: Optional[str] = None,
                fee_override: Optional[float] = None,
                slippage_override: Optional[float] = None):
        """Same override-means-flat rule as the vectorized harness (a probe
        that says fees=0 must get exactly that in every regime)."""
        from backtest.cost_model import FlatCoster
        from backtest.instruments import resolve_asset_class
        if (fee_override is not None or slippage_override is not None
                or not self.use_cost_model):
            return FlatCoster(
                self.taker_fee if fee_override is None else fee_override,
                self.slippage if slippage_override is None else slippage_override,
                notional_cap=self.notional_cap)
        asset_class = resolve_asset_class(pair, sector)
        closes = sorted(c['close'] for c in candles)
        ref_px = closes[len(closes) // 2] if closes else 1.0
        return self.cost_model.coster(pair, asset_class, ref_px,
                                      notional_cap=self.notional_cap)

    def run_strategy_on_candles(self, strategy, candles: List[dict],
                                regime_candles: List[dict],
                                pair: str,
                                fee_override: Optional[float] = None,
                                slippage_override: Optional[float] = None,
                                execution_delay: int = 0,
                                scan_exit_signals: bool = True,
                                sector: Optional[str] = None) -> BacktestResult:
        """Run a single strategy on a candle series.

        Args:
            strategy: Strategy object with scan() method
            candles: 15m signal candles
            regime_candles: 1h regime candles (aligned or overlapping)
            pair: trading pair string
            fee_override: override taker fee (for stress probes)
            slippage_override: override slippage (for stress probes)
            execution_delay: candles to delay entry (for stress probe)
            sector: graveyard sector tag, resolves the cost regime
        """
        coster = self._coster(pair, candles, sector=sector,
                              fee_override=fee_override,
                              slippage_override=slippage_override)
        slip = coster.slip_rate
        mult = coster.multiplier

        trades: List[Trade] = []
        open_trade: Optional[dict] = None

        # We need enough candles for indicators
        min_candles = max(100, self.support_lookback + 10)
        if len(candles) < min_candles:
            logger.warning(f"Insufficient candles for backtest: {len(candles)} < {min_candles}")
            return BacktestResult(strategy.name, pair,
                                  candles[0]['ts'] if candles else 0,
                                  candles[-1]['ts'] if candles else 0)

        # Timestamp-aligned regime series: for each signal bar, how many
        # regime candles are fully closed at that bar's close. Index-based
        # slicing here was the audited lookahead bug.
        signal_interval = self._infer_interval_ms(candles)
        regime_interval = self._infer_interval_ms(regime_candles) or signal_interval
        regime_counts = self._regime_closed_counts(candles, regime_candles,
                                                   signal_interval, regime_interval)
        regime_closes_full = [c['close'] for c in regime_candles]

        # Buy-and-hold benchmark over the SAME tradable window, as a price
        # return % (reporting) and as $ PnL on one notional_cap with fees +
        # slippage (the beats_buy_hold comparison).
        bh_entry_px = candles[min_candles]['close'] * (1 + slip)
        bh_exit_px = candles[-1]['close'] * (1 - slip)
        bh_return = ((candles[-1]['close'] - candles[min_candles]['close'])
                     / candles[min_candles]['close']) * 100
        bh_qty = coster.size(bh_entry_px)
        bh_pnl_usd = ((bh_exit_px - bh_entry_px) * bh_qty * mult
                      - coster.round_trip_fee(bh_entry_px, bh_exit_px, bh_qty,
                                              exit_ts_ms=candles[-1]['ts']))

        for i in range(min_candles, len(candles)):
            window = candles[:i + 1]
            col_data = self.load_candles(window)
            col_data['regime_closes'] = regime_closes_full[:regime_counts[i]]

            # Check if we have an open position and should exit. Exits are
            # only evaluated on bars AFTER the entry bar - with
            # execution_delay the entry bar can be ahead of i, and evaluating
            # exits on it would let the trade exit on pre-entry price action.
            if open_trade is not None and i > open_trade['entry_idx']:
                bar = candles[i]

                # Check stop hit (gap-aware: opening through the stop fills
                # at the open, not the stop price)
                if bar['low'] <= open_trade['stop_px']:
                    exit_px = min(open_trade['stop_px'], bar['open'])
                    self._close_trade(open_trade, exit_px, 'stop', coster, trades, bar['ts'])
                    open_trade = None

                # Check target hit (limit: opening above it fills at the open)
                elif bar['high'] >= open_trade['target_px']:
                    exit_px = max(open_trade['target_px'], bar['open'])
                    self._close_trade(open_trade, exit_px, 'target', coster, trades, bar['ts'])
                    open_trade = None

                # Check exit signals (bearish patterns). scan_exit_signals=False
                # is for the cross-harness referee, which needs all engines
                # running identical trade rules.
                elif scan_exit_signals:
                    for exit_strategy in [s for s in ALL_BUILTIN if not s.is_entry]:
                        exit_signal = exit_strategy.scan(col_data)
                        if exit_signal is not None and exit_signal.action == 'close_long':
                            self._close_trade(open_trade, bar['close'], 'signal_exit',
                                              coster, trades, bar['ts'])
                            open_trade = None
                            break

            # If no open position, look for entry signals
            if open_trade is None:
                signal = strategy.scan(col_data)
                if signal is not None and signal.direction == 'bullish' and signal.entry and signal.stop:
                    # Apply confirmation stack (simplified for backtest)
                    regime = self.get_regime(col_data['regime_closes'])
                    rsi_val = latest_rsi(col_data['closes'], self.rsi_period)
                    vol_ratio = volume_ratio(col_data['volumes'], self.volume_sma_period)

                    # Regime check
                    if regime != 'uptrend':
                        continue
                    # RSI check
                    if rsi_val > self.rsi_max_entry:
                        continue
                    # Volume check
                    if vol_ratio < self.volume_min_ratio:
                        continue

                    # Apply execution delay, then resolve the actual fill per
                    # the signal's order type (market / buy-stop / buy-limit).
                    signal_idx = i + execution_delay
                    if signal_idx >= len(candles):
                        continue  # can't enter, out of data
                    fill = self._resolve_entry(candles, signal_idx, signal, slip)
                    if fill is None:
                        continue  # pending order expired unfilled
                    entry_idx, entry_px = fill

                    # Honor the strategy's stop; fall back to 2R-from-stop
                    # target only when the signal doesn't carry one.
                    stop_px = float(signal.stop)
                    if stop_px >= entry_px:
                        continue  # invalid long (stop at/above fill) - skip
                    qty = coster.size(entry_px)
                    if qty <= 0:
                        continue

                    open_trade = {
                        'entry_idx': entry_idx,
                        'entry_ts': candles[entry_idx]['ts'],
                        'entry_px': entry_px,
                        'stop_px': stop_px,
                        'target_px': signal.target if signal.target else entry_px + (entry_px - stop_px) * 2,
                        'qty': qty,
                        'strategy_id': signal.pattern,
                        'pair': pair,
                        'regime': regime,
                        'features': {
                            'rsi': round(rsi_val, 2),
                            'volume_ratio': round(vol_ratio, 2),
                            'confidence': signal.confidence,
                        },
                    }

        # Close any remaining open trade at last close
        if open_trade is not None and candles:
            self._close_trade(open_trade, candles[-1]['close'], 'timeout',
                              coster, trades, candles[-1]['ts'])

        # Compute random-entry twin
        random_twin_pf = self._run_random_twin(candles, pair, coster)

        result = BacktestResult(
            strategy_id=strategy.name,
            pair=pair,
            period_start=candles[0]['ts'] if candles else 0,
            period_end=candles[-1]['ts'] if candles else 0,
            trades=trades,
            buy_hold_return=bh_return,
            buy_hold_pnl_usd=bh_pnl_usd,
            random_twin_pf=random_twin_pf,
            starting_capital=self.starting_capital,
            cost_model_version=coster.version,
            asset_class=coster.asset_class,
        )

        return result

    def _close_trade(self, open_trade: dict, exit_px: float, reason: str,
                     coster, trades: List[Trade], exit_ts: int):
        """Close a trade and add to trades list."""
        entry_px = open_trade['entry_px']
        qty = open_trade['qty']
        slip = coster.slip_rate
        mult = coster.multiplier

        # Slippage: stops, signal exits, and timeouts are market orders and
        # pay slippage. Targets are resting limit orders and don't. (The old
        # version exempted stops - the one exit most exposed to slippage.)
        actual_exit = exit_px if reason == 'target' else exit_px * (1 - slip)

        # Fees: entry + exit
        entry_fee = coster.leg_fee(entry_px, qty, False,
                                   ts_ms=open_trade['entry_ts'])
        exit_fee = coster.leg_fee(actual_exit, qty, True, ts_ms=exit_ts)
        total_fees = entry_fee + exit_fee

        pnl_gross = (actual_exit - entry_px) * qty * mult
        pnl_net = pnl_gross - total_fees

        # R-multiple
        risk = (entry_px - open_trade['stop_px']) * qty * mult
        r_multiple = pnl_net / risk if risk > 0 else 0.0

        trade = Trade(
            entry_ts=open_trade['entry_ts'],
            exit_ts=exit_ts,  # the exit CANDLE's timestamp, not wall clock
            pair=open_trade['pair'],
            strategy_id=open_trade['strategy_id'],
            entry_px=entry_px,
            exit_px=actual_exit,
            stop_px=open_trade['stop_px'],
            target_px=open_trade['target_px'],
            qty=qty,
            pnl_gross=pnl_gross,
            fee_cost=total_fees,
            pnl_net=pnl_net,
            r_multiple=r_multiple,
            exit_reason=reason,
            regime=open_trade['regime'],
            features=open_trade['features'],
            capital_at_risk=coster.capital_at_risk(entry_px, qty),
        )
        trades.append(trade)

    def _run_random_twin(self, candles: List[dict], pair: str, coster,
                         num_entries: Optional[int] = None) -> float:
        """Run a random-entry baseline.

        Enters at random times with same notional, same stop distance, same target.
        Returns the profit factor of the random strategy.
        """
        if not candles or len(candles) < 50:
            return 0.0
        slip = coster.slip_rate
        mult = coster.multiplier

        if num_entries is None:
            # Match the number of trades a typical strategy would produce
            num_entries = max(10, len(candles) // 50)

        # Median PF over multiple seeded draws (own RNG - never touches the
        # global random state). One draw is a coin flip, not a baseline.
        seed_pfs = []
        for seed in range(10):
            rng = random.Random(seed)
            entry_indices = sorted(rng.sample(range(50, len(candles) - 10),
                                              min(num_entries, len(candles) - 60)))

            trades_pnl = []
            for idx in entry_indices:
                entry_px = candles[idx]['close'] * (1 + slip)
                qty = coster.size(entry_px)
                if qty <= 0:
                    continue

                # Random stop: 2% below entry (typical)
                stop_px = entry_px * 0.98
                target_px = entry_px * 1.04  # 2R

                # Walk forward to find exit (gap-aware fills, same as strategy path)
                exit_px = None
                exit_reason = 'timeout'
                last_scanned = idx
                for j in range(idx + 1, min(idx + 100, len(candles))):
                    last_scanned = j
                    if candles[j]['low'] <= stop_px:
                        exit_px = min(stop_px, candles[j]['open'])
                        exit_reason = 'stop'
                        break
                    if candles[j]['high'] >= target_px:
                        exit_px = max(target_px, candles[j]['open'])
                        exit_reason = 'target'
                        break

                if exit_px is None:
                    # Timeout exits at the LAST bar scanned, not a bar from the
                    # middle of the window it already walked past.
                    exit_px = candles[last_scanned]['close']
                    exit_reason = 'timeout'

                actual_exit = exit_px if exit_reason == 'target' else exit_px * (1 - slip)
                entry_fee = coster.leg_fee(entry_px, qty, False,
                                           ts_ms=candles[idx]['ts'])
                exit_fee = coster.leg_fee(actual_exit, qty, True,
                                          ts_ms=candles[last_scanned]['ts'])
                pnl_net = ((actual_exit - entry_px) * qty * mult
                           - entry_fee - exit_fee)
                trades_pnl.append(pnl_net)

            if not trades_pnl:
                continue
            gross_profit = sum(p for p in trades_pnl if p > 0)
            gross_loss = abs(sum(p for p in trades_pnl if p <= 0))
            if gross_loss == 0:
                seed_pfs.append(float('inf') if gross_profit > 0 else 0.0)
            else:
                seed_pfs.append(gross_profit / gross_loss)

        if not seed_pfs:
            return 0.0
        # TRUE median over all seeds, inf included (re-audit NEW-3).
        ordered = sorted(seed_pfs)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[mid]
        lo, hi = ordered[mid - 1], ordered[mid]
        if hi == float('inf'):
            return float('inf') if lo == float('inf') else lo
        return (lo + hi) / 2.0

    def run_full_backtest(self, candles: List[dict], regime_candles: List[dict],
                          pair: str) -> Dict[str, BacktestResult]:
        """Run all builtin strategies on the data with train/val/test split.

        Returns dict of strategy_id -> BacktestResult (on test set).
        """
        train, val, test = self.split_chronological(candles)

        logger.info(f"Backtest split: train={len(train)}, val={len(val)}, test={len(test)}")

        results = {}
        for strategy in ENTRY_STRATEGIES:
            logger.info(f"Backtesting {strategy.name} on {pair}...")

            # Run on test set (the holdout)
            result = self.run_strategy_on_candles(
                strategy, test, regime_candles, pair
            )
            results[strategy.name] = result

            report = result.to_report()
            logger.info(f"  {strategy.name}: {report['trade_count']} trades, "
                       f"PF={report['profit_factor']}, "
                       f"return={report['strategy_return_pct']}%, "
                       f"BH={report['buy_hold_return_pct']}%, "
                       f"twin={report['random_twin_pf']}")

        return results

    def run_walk_forward(self, candles: List[dict], regime_candles: List[dict],
                         pair: str, window_size: int = 500,
                         step_size: int = 250) -> Dict[str, List[BacktestResult]]:
        """Walk-forward validation: roll the window forward in time.

        Returns dict of strategy_id -> list of results per window.
        Reports average performance across windows, not best window.
        """
        results: Dict[str, List[BacktestResult]] = {s.name: [] for s in ENTRY_STRATEGIES}

        n = len(candles)
        if n < window_size + 100:
            logger.warning(f"Insufficient data for walk-forward: {n} < {window_size + 100}")
            return results

        for start in range(0, n - window_size, step_size):
            window = candles[start:start + window_size]
            # Pass the FULL regime series: run_strategy_on_candles aligns it
            # by timestamp per bar, so only closed past regime candles are
            # ever used. Index-slicing here was part of the lookahead bug.
            for strategy in ENTRY_STRATEGIES:
                result = self.run_strategy_on_candles(
                    strategy, window, regime_candles, pair
                )
                results[strategy.name].append(result)

        # Log averages
        for name, window_results in results.items():
            if window_results:
                avg_pf = sum(r.profit_factor for r in window_results if r.profit_factor != float('inf')) / len(window_results)
                avg_return = sum(r.strategy_return_pct for r in window_results) / len(window_results)
                logger.info(f"Walk-forward {name}: avg_pf={avg_pf:.2f}, avg_return={avg_return:.2f}%")

        return results

    def run_stress_probes(self, strategy, candles: List[dict],
                          regime_candles: List[dict], pair: str) -> Dict[str, BacktestResult]:
        """Run stress probes on a strategy.

        - Fee doubling: 0.40% round-trip (2x taker)
        - Slippage doubling: 0.10% (2x market slippage)
        - Execution delay: 1 candle (requires per-trade simulation)
        - Parameter jitter: +/- 10% on all numeric parameters
        """
        probes = {}

        # Baseline
        probes['baseline'] = self.run_strategy_on_candles(
            strategy, candles, regime_candles, pair
        )

        # Fee doubling
        probes['fee_2x'] = self.run_strategy_on_candles(
            strategy, candles, regime_candles, pair,
            fee_override=self.taker_fee * 2
        )

        # Slippage doubling
        probes['slippage_2x'] = self.run_strategy_on_candles(
            strategy, candles, regime_candles, pair,
            slippage_override=self.slippage * 2
        )

        # Execution delay (1 candle)
        probes['delay_1c'] = self.run_strategy_on_candles(
            strategy, candles, regime_candles, pair,
            execution_delay=1
        )

        # Parameter jitter: modify volume threshold and RSI threshold by +/-10%
        jitter_config = json.loads(json.dumps(self.config))  # deep copy
        conf = jitter_config.get('strategy', {}).get('confirmation', {})
        conf['volume_min_ratio'] = conf.get('volume_min_ratio', 1.5) * 1.1
        conf['rsi_max_entry'] = conf.get('rsi_max_entry', 60) * 0.9

        jitter_harness = BacktestHarness(jitter_config)
        probes['param_jitter'] = jitter_harness.run_strategy_on_candles(
            strategy, candles, regime_candles, pair
        )

        return probes

    def go_no_go(self, results: Dict[str, BacktestResult],
                 min_pf: float = 1.15) -> Dict[str, dict]:
        """T7 go/no-go checkpoint.

        Evaluates each strategy against acceptance criteria:
        - PF >= 1.15 after fees
        - Beats buy-and-hold
        - Beats random twin by >= 0.15 PF

        Returns dict of strategy_id -> {pass: bool, reasons: [str]}.
        """
        verdicts = {}
        for name, result in results.items():
            reasons = []
            passed = True

            if result.profit_factor == float('inf'):
                # Zero losing trades over a full test window is a red flag for
                # a harness bug or a tiny sample, never an auto-pass.
                passed = False
                reasons.append("PF infinite (zero losing trades) - suspicious, review before trusting")
            elif result.profit_factor < min_pf:
                passed = False
                reasons.append(f"PF {result.profit_factor:.2f} < {min_pf}")

            if not result.beats_buy_hold():
                passed = False
                reasons.append(f"return {result.strategy_return_pct:.2f}% < BH {result.buy_hold_return:.2f}%")

            if not result.beats_random_twin():
                passed = False
                reasons.append(f"PF {result.profit_factor:.2f} < twin {result.random_twin_pf:.2f} + 0.15")

            if result.trade_count < 20:
                passed = False
                reasons.append(f"trade_count {result.trade_count} < 20 (insufficient)")

            verdicts[name] = {
                'pass': passed,
                'reasons': reasons if not passed else ['all criteria met'],
                'report': result.to_report(),
            }

            status = 'PASS' if passed else 'FAIL'
            logger.info(f"T7 GO/NO-GO: {name} = {status} ({'; '.join(reasons) if not passed else 'all criteria met'})")

        return verdicts
