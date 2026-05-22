"""
BinBot AI Auto Mode — Celery Worker
Background tasks: scanner scheduling, ML model retraining, performance snapshots.
"""

from celery import Celery
from celery.schedules import crontab
import os

# Initialize Celery
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("binbot", broker=redis_url, backend=redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# ── Periodic Tasks ───────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Save daily performance snapshot at midnight UTC
    "daily-performance-snapshot": {
        "task": "celery_worker.save_daily_snapshot",
        "schedule": crontab(hour=0, minute=5),
    },
    # Retrain ML model weekly on Sunday at 2am UTC
    "weekly-ml-retrain": {
        "task": "celery_worker.retrain_ml_model",
        "schedule": crontab(hour=2, minute=0, day_of_week=0),
    },
    # Clean old logs (keep last 7 days)
    "daily-log-cleanup": {
        "task": "celery_worker.cleanup_old_logs",
        "schedule": crontab(hour=1, minute=0),
    },
}


@celery_app.task(name="celery_worker.save_daily_snapshot")
def save_daily_snapshot():
    """Save daily performance metrics to the database."""
    import asyncio
    asyncio.run(_save_daily_snapshot_async())


async def _save_daily_snapshot_async():
    """Async implementation of daily snapshot."""
    from app.db.session import async_session_factory
    from app.models import Bot, PerformanceMetric, Trade, TradeStatus
    from sqlalchemy import select, func, and_
    from datetime import date, datetime

    async with async_session_factory() as session:
        result = await session.execute(select(Bot))
        bots = result.scalars().all()

        for bot in bots:
            today = date.today()

            # Check if already snapshotted
            existing = await session.execute(
                select(PerformanceMetric)
                .where(and_(
                    PerformanceMetric.bot_id == bot.id,
                    PerformanceMetric.date == today,
                ))
            )
            if existing.scalar_one_or_none():
                continue

            # Calculate daily stats
            today_start = datetime.combine(today, datetime.min.time())
            trades_result = await session.execute(
                select(Trade)
                .where(and_(
                    Trade.bot_id == bot.id,
                    Trade.status == TradeStatus.CLOSED,
                    Trade.exit_time >= today_start,
                ))
            )
            trades = trades_result.scalars().all()

            total = len(trades)
            wins = len([t for t in trades if t.realized_pnl > 0])
            daily_pnl = sum(t.realized_pnl for t in trades)

            metric = PerformanceMetric(
                bot_id=bot.id,
                date=today,
                starting_equity=bot.daily_starting_equity,
                ending_equity=bot.daily_starting_equity + daily_pnl,
                daily_pnl=daily_pnl,
                daily_pnl_pct=(daily_pnl / bot.daily_starting_equity * 100)
                if bot.daily_starting_equity > 0 else 0,
                total_trades=total,
                winning_trades=wins,
                win_rate=(wins / total * 100) if total > 0 else 0,
            )
            session.add(metric)

        await session.commit()


@celery_app.task(name="celery_worker.retrain_ml_model")
def retrain_ml_model():
    """Retrain the XGBoost ML model on historical trades."""
    import asyncio
    asyncio.run(_retrain_async())


async def _retrain_async():
    """Async ML retraining."""
    from app.db.session import async_session_factory
    from app.models import Trade, TradeStatus, Signal
    from app.engine.ml_engine import MLConfirmationEngine
    from sqlalchemy import select, and_
    from datetime import datetime, timedelta
    import pandas as pd
    import logging

    logger = logging.getLogger("celery.ml_retrain")

    async with async_session_factory() as session:
        cutoff = datetime.utcnow() - timedelta(days=30)
        result = await session.execute(
            select(Trade, Signal)
            .join(Signal, Trade.signal_id == Signal.id, isouter=True)
            .where(and_(
                Trade.status == TradeStatus.CLOSED,
                Trade.exit_time >= cutoff,
            ))
        )
        rows = result.all()

        if len(rows) < 50:
            logger.info(f"Only {len(rows)} trades in last 30 days. Need 50+ for retraining.")
            return

        # Build training DataFrame
        records = []
        for trade, signal in rows:
            if signal and signal.features_snapshot:
                record = {**signal.features_snapshot}
                record["target"] = 1 if trade.realized_pnl > 0 else 0
                records.append(record)

        if len(records) < 50:
            logger.info("Not enough feature data for retraining.")
            return

        df = pd.DataFrame(records)
        ml = MLConfirmationEngine()
        ml.train(df)
        logger.info(f"ML model retrained on {len(df)} samples")


@celery_app.task(name="celery_worker.cleanup_old_logs")
def cleanup_old_logs():
    """Remove log entries older than 7 days."""
    import asyncio
    asyncio.run(_cleanup_logs_async())


async def _cleanup_logs_async():
    """Async log cleanup."""
    from app.db.session import async_session_factory
    from app.models import Log
    from sqlalchemy import delete
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=7)
    async with async_session_factory() as session:
        result = await session.execute(
            delete(Log).where(Log.created_at < cutoff)
        )
        await session.commit()
