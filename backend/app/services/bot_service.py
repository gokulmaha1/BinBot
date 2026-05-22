"""
BinBot AI Auto Mode — Bot Orchestrator Service
The main brain that connects Scanner → Features → Regime → Strategy → Score → ML → Risk → Execute.
"""

import asyncio
import logging
from datetime import datetime, date
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.config import settings
from app.models import (
    Bot, BotStatus, Trade, TradeStatus, Signal, SignalSide,
    SignalStatus, Strategy, StrategyType, Log, LogLevel, LogSource,
)
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


class BotService:
    """
    AI Auto Mode Orchestrator.
    
    Pipeline:
    1. Scanner → Find top pairs
    2. Features → Extract indicators for each pair
    3. Regime → Detect market conditions
    4. Strategies → Run compatible strategies
    5. Scoring → Score all signals
    6. ML → Confirm top signals
    7. Risk → Validate against risk rules
    8. Execute → Place trades
    9. Monitor → Watch open positions
    """

    def __init__(self):
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._bot_id: Optional[UUID] = None

        # Engine modules (lazy-loaded to avoid circular imports)
        self._scanner = None
        self._features = None
        self._regime = None
        self._strategies = None
        self._scorer = None
        self._ml = None
        self._risk = None
        self._executor = None
        self._monitor = None
        self._notifier = None

    async def _init_engines(self):
        """Initialize all engine modules."""
        from app.engine.scanner import PairScanner
        from app.engine.features import FeatureExtractor
        from app.engine.regime import RegimeDetector
        from app.engine.strategies import StrategyEngine
        from app.engine.scoring import SignalScorer
        from app.engine.ml_engine import MLConfirmationEngine
        from app.engine.risk import RiskManager
        from app.engine.executor import TradeExecutor
        from app.engine.monitor import PositionMonitor
        from app.services.notification import NotificationService
        from app.deps import get_redis

        redis = await get_redis()

        self._scanner = PairScanner(redis=redis)
        # Initialize the scanner's Binance client so scan() can fetch data.
        # We don't call scanner.start() because bot_service has its own loop.
        from binance import AsyncClient
        if settings.is_testnet:
            self._scanner._binance_client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
                testnet=True,
            )
        else:
            self._scanner._binance_client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
            )

        self._features = FeatureExtractor(redis=redis)
        self._regime = RegimeDetector()
        self._strategies = StrategyEngine()
        self._scorer = SignalScorer()
        self._ml = MLConfirmationEngine()
        self._risk = RiskManager(redis=redis)
        self._executor = TradeExecutor()
        self._monitor = PositionMonitor()
        self._notifier = NotificationService()

        logger.info("All engine modules initialized")

    async def start(self, bot_id: str, paused: bool = False):
        """Start the AI Auto Mode loop."""
        if isinstance(bot_id, str):
            bot_uuid = UUID(bot_id)
        else:
            bot_uuid = bot_id

        if self._running:
            if self._paused and not paused:
                self._paused = False
                logger.info("AI Auto Mode resumed from paused state")
                if self._notifier:
                    await self._notifier.notify_bot_status("running", "AI Auto Mode resumed")
            else:
                logger.warning("Bot is already running")
            return

        self._bot_id = bot_uuid
        self._running = True
        self._paused = paused

        await self._init_engines()

        self._task = asyncio.create_task(self._main_loop())
        logger.info(f"AI Auto Mode started for bot {bot_uuid}")

        if self._notifier:
            await self._notifier.notify_bot_status(
                "paused" if paused else "running",
                "AI Auto Mode activated (paused)" if paused else "AI Auto Mode activated"
            )

    async def stop(self):
        """Stop the bot loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("AI Auto Mode stopped")
        if self._notifier:
            await self._notifier.notify_bot_status("stopped", "Bot stopped by user")

    async def pause(self):
        """Pause new trade scanning (keep monitoring positions)."""
        self._paused = True
        logger.info("AI Auto Mode paused — monitoring only")

    async def _log_to_db(self, level: LogLevel, source: LogSource, message: str):
        """Write a log entry to the database."""
        try:
            async with async_session_factory() as session:
                log = Log(
                    bot_id=self._bot_id,
                    level=level,
                    source=source,
                    message=message,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to write log to DB: {e}")

        # Also broadcast to dashboard
        from app.api.websocket import broadcast_log
        await broadcast_log(level.value, source.value, message)

    async def _reset_daily_if_needed(self, session: AsyncSession, bot: Bot):
        """Reset daily counters at midnight."""
        today = date.today()
        if bot.last_reset_date != today:
            bot.daily_pnl = 0.0
            bot.trades_today = 0
            bot.consecutive_losses = 0
            bot.cooldown_until = None
            bot.last_reset_date = today

            # Snapshot yesterday's equity
            balance = await self._executor.get_balance() if self._executor else 0
            bot.daily_starting_equity = balance
            if balance > bot.peak_equity:
                bot.peak_equity = balance

            await session.commit()
            logger.info(f"Daily stats reset. Starting equity: ${balance:.2f}")

    async def _main_loop(self):
        """
        The core AI Auto Mode loop.
        
        Runs every scanner interval, executing the full pipeline:
        Scan → Extract → Regime → Strategy → Score → ML → Risk → Execute
        """
        logger.info("Entering main trading loop...")

        # Log active config on startup so user can see all parameters
        await self._log_to_db(
            LogLevel.INFO, LogSource.SCANNER,
            f"⚙️ CONFIG: Mode={settings.TRADING_MODE.value} | "
            f"SignalThreshold={settings.SIGNAL_SCORE_THRESHOLD} | "
            f"MLThreshold={settings.ML_CONFIDENCE_THRESHOLD:.0%} | "
            f"RiskPerTrade={settings.MAX_RISK_PER_TRADE:.0%} | "
            f"CapitalPerTrade={settings.CAPITAL_PER_TRADE_PCT:.0%} | "
            f"MaxPositions={settings.MAX_ACTIVE_POSITIONS} | "
            f"MaxTrades/Day={settings.MAX_TRADES_PER_DAY}"
        )
        await self._log_to_db(
            LogLevel.INFO, LogSource.SCANNER,
            f"⚙️ TP TIERS: TP1={settings.TP1_RATIO}R close {settings.TP1_CLOSE_PCT:.0%} | "
            f"TP2={settings.TP2_RATIO}R close {settings.TP2_CLOSE_PCT:.0%} | "
            f"TP3={settings.TP3_RATIO}R close {settings.TP3_CLOSE_PCT:.0%} | "
            f"ScanInterval={settings.SCANNER_INTERVAL_SECONDS}s | "
            f"ManualPairs={settings.SCANNER_MANUAL_PAIRS or 'auto'}"
        )

        while self._running:
            try:
                async with async_session_factory() as session:
                    # Get bot record
                    result = await session.execute(
                        select(Bot).where(Bot.id == self._bot_id)
                    )
                    bot = result.scalar_one_or_none()
                    if not bot:
                        logger.error(f"Bot {self._bot_id} not found in DB")
                        await asyncio.sleep(10)
                        continue

                    # Reset daily counters if new day
                    await self._reset_daily_if_needed(session, bot)

                    # ── MONITOR: Always check open positions ─────
                    if self._monitor and self._executor:
                        try:
                            await self._monitor.check_positions(
                                bot_id=self._bot_id,
                                session=session,
                                executor=self._executor,
                                risk_manager=self._risk,
                                notifier=self._notifier,
                            )
                        except Exception as e:
                            logger.error(f"Position monitor error: {e}")

                    # ── PAUSE CHECK: Skip scanning if paused ─────
                    if self._paused:
                        await asyncio.sleep(settings.SCANNER_INTERVAL_SECONDS)
                        continue

                    # ── PRE-FLIGHT: Check if we can trade ────────
                    if not await self._can_trade(session, bot):
                        await asyncio.sleep(settings.SCANNER_INTERVAL_SECONDS)
                        continue

                    # ── STEP 1: SCAN — Find top pairs ────────────
                    await self._log_to_db(
                        LogLevel.INFO, LogSource.SCANNER, "Scanning market for opportunities..."
                    )
                    ranked_pairs = await self._scanner.scan()

                    if not ranked_pairs:
                        await self._log_to_db(
                            LogLevel.INFO, LogSource.SCANNER, "🔍 Scanner found 0 candidate pairs."
                        )
                        await asyncio.sleep(settings.SCANNER_INTERVAL_SECONDS)
                        continue

                    top_symbols = [p["symbol"] for p in ranked_pairs[:5]]
                    symbols_str = ", ".join(top_symbols)
                    suffix = "..." if len(ranked_pairs) > 5 else ""
                    await self._log_to_db(
                        LogLevel.INFO, LogSource.SCANNER,
                        f"🔍 Scanner found {len(ranked_pairs)} candidate pairs. Analyzing top symbols: {symbols_str}{suffix}"
                    )

                    from app.api.websocket import broadcast_scanner
                    await broadcast_scanner(ranked_pairs)

                    # ── STEP 2-6: ANALYZE each pair ──────────────
                    all_opportunities = []
                    no_features_pairs = []
                    no_signals_pairs = []

                    for pair_info in ranked_pairs:
                        symbol = pair_info["symbol"]

                        try:
                            # Step 2: Extract features
                            # Fetch klines from Binance to build DataFrame for feature extraction
                            import pandas as pd
                            try:
                                klines_raw = await self._scanner._binance_client.futures_klines(
                                    symbol=symbol,
                                    interval="1h",
                                    limit=200,
                                )
                                if len(klines_raw) < 50:
                                    no_features_pairs.append(symbol)
                                    continue

                                df = pd.DataFrame(klines_raw, columns=[
                                    'open_time', 'open', 'high', 'low', 'close', 'volume',
                                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                    'taker_buy_quote', 'ignore'
                                ])
                                for col in ['open', 'high', 'low', 'close', 'volume']:
                                    df[col] = df[col].astype(float)
                            except Exception as kline_err:
                                logger.error(f"Failed to fetch klines for {symbol}: {kline_err}")
                                no_features_pairs.append(symbol)
                                continue

                            features = await self._features.extract(df, symbol)
                            if features is None:
                                no_features_pairs.append(symbol)
                                continue

                            # Step 3: Detect regime
                            regime = self._regime.detect(features)
                            await self._log_to_db(
                                LogLevel.INFO, LogSource.DATA,
                                f"📊 {symbol}: Regime={regime.regime} | RSI={getattr(features, 'rsi', 'N/A')} | "
                                f"ADX={getattr(features, 'adx', 'N/A')} | EMA_stack={getattr(features, 'ema_bullish', 'N/A')}"
                            )

                            # Step 4: Run strategies
                            signals = self._strategies.evaluate(features, regime)
                            if not signals:
                                no_signals_pairs.append(f"{symbol} ({regime.regime})")
                                continue

                            await self._log_to_db(
                                LogLevel.INFO, LogSource.STRATEGY,
                                f"📡 {symbol}: {len(signals)} signal(s) generated — "
                                + ", ".join([f"{s.strategy_name} {s.side.value}" for s in signals])
                            )

                            # Step 5: Score signals
                            for signal in signals:
                                scored = self._scorer.score(signal, features, regime)

                                if scored.total_score < settings.SIGNAL_SCORE_THRESHOLD:
                                    # Record rejected signal
                                    await self._record_signal(
                                        session, bot.id, symbol, signal, scored,
                                        ml_confidence=0,
                                        status=SignalStatus.REJECTED,
                                        reason=f"Score {scored.total_score} < {settings.SIGNAL_SCORE_THRESHOLD}",
                                        regime=regime.regime,
                                    )
                                    await self._log_to_db(
                                        LogLevel.WARNING, LogSource.STRATEGY,
                                        f"⚠️ {symbol} ({regime.regime}) Strategy {signal.strategy_name} rejected: Score {scored.total_score} < threshold {settings.SIGNAL_SCORE_THRESHOLD}"
                                    )
                                    continue

                                # Step 6: ML confirmation
                                ml_result = await self._ml.predict(scored.to_features_dict(features, regime))

                                if ml_result.probability < settings.ML_CONFIDENCE_THRESHOLD:
                                    await self._record_signal(
                                        session, bot.id, symbol, signal, scored,
                                        ml_confidence=ml_result.probability,
                                        status=SignalStatus.REJECTED,
                                        reason=f"ML confidence {ml_result.probability:.1%} < {settings.ML_CONFIDENCE_THRESHOLD:.0%}",
                                        regime=regime.regime,
                                    )
                                    await self._log_to_db(
                                        LogLevel.WARNING, LogSource.ML,
                                        f"🤖 {symbol} ({regime.regime}) Strategy {signal.strategy_name} rejected: ML confidence {ml_result.probability:.1%} < threshold {settings.ML_CONFIDENCE_THRESHOLD:.0%}"
                                    )
                                    continue

                                # Signal passed all checks!
                                await self._log_to_db(
                                    LogLevel.INFO, LogSource.STRATEGY,
                                    f"✅ {symbol} ({regime.regime}) Strategy {signal.strategy_name} ACCEPTED! Score: {scored.total_score}, ML: {ml_result.probability:.1%}"
                                )

                                all_opportunities.append({
                                    "symbol": symbol,
                                    "signal": signal,
                                    "scored": scored,
                                    "ml_result": ml_result,
                                    "features": features,
                                    "regime": regime,
                                })

                        except Exception as e:
                            logger.error(f"Error analyzing {symbol}: {e}")
                            continue

                    # Log silent skips in summary
                    if no_features_pairs:
                        await self._log_to_db(
                            LogLevel.INFO, LogSource.DATA,
                            f"ℹ️ Silent skip (no features extracted): {', '.join(no_features_pairs)}"
                        )
                    if no_signals_pairs:
                        await self._log_to_db(
                            LogLevel.INFO, LogSource.STRATEGY,
                            f"ℹ️ Silent skip (no strategy signals): {', '.join(no_signals_pairs)}"
                        )

                    # Log cycle summary funnel
                    total_scored = len(all_opportunities)
                    total_rejected_score = sum(1 for _ in no_signals_pairs)  # approximate
                    await self._log_to_db(
                        LogLevel.INFO, LogSource.SCANNER,
                        f"📈 CYCLE SUMMARY: {len(ranked_pairs)} scanned → "
                        f"{len(ranked_pairs) - len(no_features_pairs)} features → "
                        f"{len(ranked_pairs) - len(no_features_pairs) - len(no_signals_pairs)} signals → "
                        f"{total_scored} opportunities passed all checks"
                    )

                    # ── STEP 7: RANK & SELECT best opportunities ─
                    if not all_opportunities:
                        await asyncio.sleep(settings.SCANNER_INTERVAL_SECONDS)
                        continue

                    # Sort by composite score (signal score * ML confidence)
                    all_opportunities.sort(
                        key=lambda x: x["scored"].total_score * x["ml_result"].probability,
                        reverse=True,
                    )

                    await self._log_to_db(
                        LogLevel.INFO, LogSource.STRATEGY,
                        f"Found {len(all_opportunities)} opportunities. "
                        f"Top: {all_opportunities[0]['symbol']} "
                        f"(score={all_opportunities[0]['scored'].total_score}, "
                        f"ML={all_opportunities[0]['ml_result'].probability:.1%})"
                    )

                    # ── STEP 8: RISK CHECK & EXECUTE ─────────────
                    for opp in all_opportunities:
                        try:
                            # Get current active position count
                            active_count = await self._get_active_position_count(session, bot.id)
                            if active_count >= settings.MAX_ACTIVE_POSITIONS:
                                await self._log_to_db(
                                    LogLevel.WARNING, LogSource.RISK,
                                    f"🚫 Max positions reached ({active_count}/{settings.MAX_ACTIVE_POSITIONS}). Skipping remaining opportunities."
                                )
                                break

                            # Risk check
                            risk_check = await self._risk.check_trade_allowed(
                                bot=bot,
                                symbol=opp["symbol"],
                                side=opp["signal"].side,
                                session=session,
                            )

                            if not risk_check.allowed:
                                await self._record_signal(
                                    session, bot.id, opp["symbol"], opp["signal"],
                                    opp["scored"], opp["ml_result"].probability,
                                    SignalStatus.REJECTED, risk_check.reason,
                                    opp["regime"].regime,
                                )
                                await self._log_to_db(
                                    LogLevel.WARNING, LogSource.RISK,
                                    f"Trade blocked for {opp['symbol']}: {risk_check.reason}"
                                )
                                continue

                            # Calculate position size
                            balance = await self._executor.get_balance()
                            equity = balance * settings.CAPITAL_PER_TRADE_PCT

                            # Fetch quantity precision from executor asynchronously
                            qty_precision = await self._executor.get_quantity_precision(opp["symbol"])

                            position_size = self._risk.calculate_position_size(
                                equity=equity,
                                entry_price=opp["signal"].entry_price,
                                sl_distance=opp["signal"].sl_distance,
                                symbol=opp["symbol"],
                                qty_precision=qty_precision,
                            )

                            # Calculate TP levels
                            tp_levels = self._risk.calculate_tp_levels(
                                entry_price=opp["signal"].entry_price,
                                sl_distance=opp["signal"].sl_distance,
                                side=opp["signal"].side,
                                total_quantity=position_size.quantity,
                            )

                            # Record signal first as ACCEPTED so its ID is available for the trade record
                            signal_record = await self._record_signal(
                                session, bot.id, opp["symbol"], opp["signal"],
                                opp["scored"], opp["ml_result"].probability,
                                SignalStatus.ACCEPTED, None, opp["regime"].regime,
                            )

                            # Bind DB session to executor dynamically
                            self._executor.db = session

                            # Calculate SL price
                            sl_price = (
                                opp["signal"].entry_price - opp["signal"].sl_distance
                                if opp["signal"].side in ("BUY", SignalSide.BUY)
                                else opp["signal"].entry_price + opp["signal"].sl_distance
                            )

                            # EXECUTE THE TRADE (supports paper & live modes)
                            trade_result = await self._executor.execute_trade(
                                signal=signal_record,
                                position_size=position_size,
                                tp_levels=tp_levels,
                                sl_price=sl_price,
                                bot_id=bot.id,
                                strategy_name=opp["signal"].strategy_name,
                            )

                            if trade_result and trade_result.success:
                                bot.trades_today += 1
                                await session.commit()

                                prefix = "[PAPER] " if settings.is_paper else ""
                                await self._log_to_db(
                                    LogLevel.TRADE, LogSource.EXECUTOR,
                                    f"🚀 {prefix}TRADE OPENED | {opp['signal'].side} {opp['symbol']} | "
                                    f"Entry: {trade_result.entry_price} | "
                                    f"SL: {sl_price} | "
                                    f"TP1: {tp_levels.tp1.price} | "
                                    f"Leverage: {position_size.leverage}x | "
                                    f"Score: {opp['scored'].total_score} | "
                                    f"ML: {opp['ml_result'].probability:.1%}"
                                )

                                # Notify via Telegram
                                if self._notifier:
                                    await self._notifier.notify_trade_entry(
                                        symbol=opp["symbol"],
                                        side=opp["signal"].side,
                                        strategy=opp["signal"].strategy_name,
                                        entry_price=trade_result.entry_price,
                                        quantity=position_size.quantity,
                                        leverage=position_size.leverage,
                                        sl_price=sl_price,
                                        tp1_price=tp_levels.tp1.price,
                                        score=opp["scored"].total_score,
                                        ml_confidence=opp["ml_result"].probability,
                                    )

                                # Broadcast to dashboard
                                from app.api.websocket import broadcast_trade
                                await broadcast_trade({
                                    "event": "opened",
                                    "symbol": opp["symbol"],
                                    "side": opp["signal"].side,
                                    "entry_price": trade_result.entry_price,
                                    "strategy": opp["signal"].strategy_name,
                                })
                            else:
                                # Revert the signal record status to REJECTED with error
                                signal_record.status = SignalStatus.REJECTED
                                signal_record.reject_reason = trade_result.error if trade_result else "Execution failed"
                                await session.commit()

                                await self._log_to_db(
                                    LogLevel.ERROR, LogSource.EXECUTOR,
                                    f"Failed to execute trade for {opp['symbol']}: {signal_record.reject_reason}"
                                )

                        except Exception as e:
                            logger.error(f"Error executing trade for {opp['symbol']}: {e}")
                            continue

                # Wait for next scan cycle
                await asyncio.sleep(settings.SCANNER_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(30)

        logger.info("Main trading loop exited")

    async def _can_trade(self, session: AsyncSession, bot: Bot) -> bool:
        """Pre-flight checks before scanning for trades."""
        # Check if bot is in error state
        if bot.status == BotStatus.ERROR:
            return False

        # Check cooldown from consecutive losses
        if bot.cooldown_until and datetime.utcnow() < bot.cooldown_until:
            remaining = (bot.cooldown_until - datetime.utcnow()).total_seconds()
            logger.info(f"Cooldown active. {remaining:.0f}s remaining.")
            return False

        # Check daily loss limit
        if bot.daily_starting_equity > 0:
            daily_loss_pct = abs(bot.daily_pnl) / bot.daily_starting_equity
            if bot.daily_pnl < 0 and daily_loss_pct >= settings.MAX_DAILY_LOSS:
                await self._log_to_db(
                    LogLevel.WARNING, LogSource.RISK,
                    f"Daily loss limit hit: {daily_loss_pct:.1%} >= {settings.MAX_DAILY_LOSS:.0%}"
                )
                return False

        # Check max drawdown
        if bot.peak_equity > 0:
            drawdown = (bot.peak_equity - (bot.daily_starting_equity + bot.daily_pnl)) / bot.peak_equity
            if drawdown >= settings.MAX_DRAWDOWN:
                await self._log_to_db(
                    LogLevel.ERROR, LogSource.RISK,
                    f"⚠️ MAX DRAWDOWN HIT: {drawdown:.1%} >= {settings.MAX_DRAWDOWN:.0%}. "
                    f"Bot paused. Manual intervention required."
                )
                bot.status = BotStatus.PAUSED
                await session.commit()
                if self._notifier:
                    await self._notifier.notify_risk_alert(
                        "MAX_DRAWDOWN", f"Drawdown {drawdown:.1%} hit limit. Bot paused."
                    )
                return False

        # Check daily trade limit
        if bot.trades_today >= settings.MAX_TRADES_PER_DAY:
            logger.info(f"Daily trade limit reached: {bot.trades_today}/{settings.MAX_TRADES_PER_DAY}")
            return False

        return True

    async def _get_active_position_count(self, session: AsyncSession, bot_id) -> int:
        """Count currently open positions."""
        from sqlalchemy import func
        result = await session.execute(
            select(func.count(Trade.id))
            .where(and_(
                Trade.bot_id == bot_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
            ))
        )
        return result.scalar() or 0

    async def _record_signal(
        self, session, bot_id, symbol, signal, scored,
        ml_confidence, status, reason, regime,
    ):
        """Record a signal in the database."""
        record = Signal(
            bot_id=bot_id,
            symbol=symbol,
            side=SignalSide(signal.side),
            strategy_name=signal.strategy_name,
            score=scored.total_score,
            ml_confidence=ml_confidence,
            score_breakdown=scored.breakdown,
            features_snapshot=signal.features_used if hasattr(signal, 'features_used') else {},
            regime=regime,
            status=status,
            reject_reason=reason,
        )
        session.add(record)
        await session.flush()

        # Broadcast to dashboard
        from app.api.websocket import broadcast_signal
        await broadcast_signal({
            "symbol": symbol,
            "side": signal.side,
            "strategy": signal.strategy_name,
            "score": scored.total_score,
            "ml_confidence": ml_confidence,
            "status": status.value,
            "reason": reason,
        })

        return record
