from fastapi import HTTPException, Depends, APIRouter
from app.models.stats import StatsRequest
from sqlalchemy.orm import Session
from app.database import get_db
from datetime import datetime, timedelta
import os
from metaapi_cloud_sdk import MetaStats
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

router = APIRouter(
    tags=['Trading Stats']
)



# ------------------------
# Save metrics to DB
# ------------------------
def save_metrics(db: Session, account_id: str, metrics: dict, daily_growth: list = None):
    try:
        params = {
            "account_id": account_id,
            "balance": metrics.get("balance", 0),
            "equity": metrics.get("equity", 0),
            "profit": metrics.get("profit", 0),
            "deposits": metrics.get("deposits", 0),
            "withdrawals": metrics.get("withdrawals", 0),
            "margin": metrics.get("margin", 0),
            "free_margin": metrics.get("freeMargin", 0),
            "trades": metrics.get("trades", 0),
            "profit_factor": metrics.get("profitFactor"),
            "sharpe_ratio": metrics.get("sharpeRatio"),
            "won_trades_percent": metrics.get("wonTradesPercent"),
            "lost_trades_percent": metrics.get("lostTradesPercent"),
            "daily_growth": daily_growth or None,
        }

        exists = db.execute(
            text("SELECT id FROM meta_account_metrics WHERE account_id = :account_id"),
            {"account_id": account_id}
        ).fetchone()

        if exists:
            db.execute(
                text("""
                UPDATE meta_account_metrics SET
                    balance = :balance,
                    equity = :equity,
                    profit = :profit,
                    deposits = :deposits,
                    withdrawals = :withdrawals,
                    margin = :margin,
                    free_margin = :free_margin,
                    trades = :trades,
                    profit_factor = :profit_factor,
                    sharpe_ratio = :sharpe_ratio,
                    won_trades_percent = :won_trades_percent,
                    lost_trades_percent = :lost_trades_percent,
                    daily_growth = :daily_growth,
                    updated_at = NOW()
                WHERE account_id = :account_id
                """),
                params
            )
        else:
            db.execute(
                text("""
                INSERT INTO meta_account_metrics
                (account_id, balance, equity, profit, deposits, withdrawals, margin, free_margin,
                 trades, profit_factor, sharpe_ratio, won_trades_percent, lost_trades_percent,
                 daily_growth, created_at, updated_at)
                VALUES
                (:account_id, :balance, :equity, :profit, :deposits, :withdrawals, :margin, :free_margin,
                 :trades, :profit_factor, :sharpe_ratio, :won_trades_percent, :lost_trades_percent,
                 :daily_growth, NOW(), NOW())
                """),
                params
            )

        db.commit()
    except Exception as e:
        db.rollback()
        raise e


# ------------------------
# Fetch stats from MetaStats
# ------------------------
async def fetch_stats(account_id: str, days: int):
    token = os.getenv("META_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MetaApi token not set in environment")

    api = MetaStats(token=token)

    try:
        metrics = await api.get_metrics(account_id=account_id)
        metrics_open = await api.get_metrics(account_id=account_id, include_open_positions=True)

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S.000")
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S.000")

        trades = await api.get_account_trades(
            account_id=account_id,
            start_time=start_str,
            end_time=end_str,
        )
        open_trades = await api.get_account_open_trades(account_id=account_id)

        daily_growth = [
            {"date": t.get("time"), "balance": t.get("balance")}
            for t in trades if "balance" in t
        ]

        return {
            "metrics": metrics,
            "metrics_with_open": metrics_open,
            "open_trades": open_trades,
            "historical_trades": trades,
            "daily_growth": daily_growth,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            api.close()
        except Exception:
            pass


# ------------------------
# Endpoint
# ------------------------
@router.post("/api/trading-stats")
async def meta_stats(request: StatsRequest, db: Session = Depends(get_db)):
    result = await fetch_stats(request.account_id, request.days)

    save_metrics(db, request.account_id, result["metrics"], daily_growth=result.get("daily_growth", []))

    summary = {
        "account_id": request.account_id,
        "balance": result["metrics"].get("balance"),
        "equity": result["metrics"].get("equity"),
        "profit": result["metrics"].get("profit"),
        "deposits": result["metrics"].get("deposits"),
        "withdrawals": result["metrics"].get("withdrawals"),
        "trades_count": result["metrics"].get("trades"),
        "open_trades_count": len(result["open_trades"]) if isinstance(result["open_trades"], list) else 0,
        "historical_trades_count": len(result["historical_trades"]) if isinstance(result["historical_trades"], list) else 0,
    }

    return {
        **result,
        "summary": summary
    }
