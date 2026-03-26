from fastapi import HTTPException, Depends, APIRouter
from app.models.stats import StatsRequest
from sqlalchemy.orm import Session
from app.database import get_db
from datetime import datetime, timedelta
from statistics import stdev, mean
import os
from metaapi_cloud_sdk import MetaStats
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

router = APIRouter(
    tags=['Trading Stats']
)


# ------------------------
# Calculate metrics from trades
# ------------------------
def calculate_metrics_from_trades(trades: list, current_balance: float, current_equity: float) -> dict:
    if not trades:
        return {
            "balance": current_balance,
            "equity": current_equity,
            "profit": 0,
            "trades": 0,
            "profitFactor": None,
            "sharpeRatio": None,
            "wonTradesPercent": None,
            "lostTradesPercent": None,
            "deposits": 0,
            "withdrawals": 0,
        }

    # Filter only actual trades (not deposits/withdrawals)
    actual_trades = [t for t in trades if t.get("type") == "DEAL_TYPE_SELL" or t.get("type") == "DEAL_TYPE_BUY"]

    if not actual_trades:
        return {
            "balance": current_balance,
            "equity": current_equity,
            "profit": 0,
            "trades": 0,
            "profitFactor": None,
            "sharpeRatio": None,
            "wonTradesPercent": None,
            "lostTradesPercent": None,
            "deposits": 0,
            "withdrawals": 0,
        }

    profits = [t.get("profit", 0) for t in actual_trades]
    total_profit = sum(profits)

    winning_trades = [p for p in profits if p > 0]
    losing_trades = [p for p in profits if p < 0]

    gross_profit = sum(winning_trades) if winning_trades else 0
    gross_loss = abs(sum(losing_trades)) if losing_trades else 0

    total_trades = len(actual_trades)
    won_count = len(winning_trades)
    lost_count = len(losing_trades)

    # Profit factor
    profit_factor = None
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = float('inf')

    # Win/Loss percentages
    won_percent = round((won_count / total_trades) * 100, 2) if total_trades > 0 else None
    lost_percent = round((lost_count / total_trades) * 100, 2) if total_trades > 0 else None

    # Sharpe ratio (simplified: mean return / std dev of returns)
    sharpe_ratio = None
    if len(profits) > 1:
        avg_return = mean(profits)
        std_return = stdev(profits)
        if std_return > 0:
            sharpe_ratio = round(avg_return / std_return, 2)

    # Deposits and withdrawals
    deposits = sum(t.get("profit", 0) for t in trades if t.get("type") == "DEAL_TYPE_BALANCE" and t.get("profit", 0) > 0)
    withdrawals = abs(sum(t.get("profit", 0) for t in trades if t.get("type") == "DEAL_TYPE_BALANCE" and t.get("profit", 0) < 0))

    return {
        "balance": current_balance,
        "equity": current_equity,
        "profit": round(total_profit, 2),
        "trades": total_trades,
        "profitFactor": profit_factor,
        "sharpeRatio": sharpe_ratio,
        "wonTradesPercent": won_percent,
        "lostTradesPercent": lost_percent,
        "deposits": deposits,
        "withdrawals": withdrawals,
    }



# ------------------------
# Save metrics to DB
# ------------------------
def save_metrics(db: Session, account_id: str, metrics: dict, daily_growth: list = None):
    try:
        profit_factor = metrics.get("profitFactor")
        if profit_factor == float('inf'):
            profit_factor = None

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
            "profit_factor": profit_factor,
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
# Get account creation date from DB
# ------------------------
def get_account_created_at(db: Session, account_id: str) -> datetime:
    result = db.execute(
        text("SELECT created_at FROM meta_trader_credentials WHERE account_id = :account_id"),
        {"account_id": account_id}
    ).fetchone()

    if result and result[0]:
        return result[0]
    return datetime.utcnow() - timedelta(days=90)


# ------------------------
# Fetch stats from MetaStats
# ------------------------
async def fetch_stats(account_id: str, created_at: datetime):
    token = os.getenv("META_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MetaApi token not set in environment")

    api = MetaStats(token=token)

    try:
        end_time = datetime.utcnow()
        start_str = created_at.strftime("%Y-%m-%d %H:%M:%S.000")
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S.000")

        # Get current balance/equity from overall metrics
        overall_metrics = await api.get_metrics(account_id=account_id)
        current_balance = overall_metrics.get("balance", 0)
        current_equity = overall_metrics.get("equity", 0)

        # Get trades from account creation date
        trades = await api.get_account_trades(
            account_id=account_id,
            start_time=start_str,
            end_time=end_str,
        )
        open_trades = await api.get_account_open_trades(account_id=account_id)

        # Calculate metrics from filtered trades
        metrics = calculate_metrics_from_trades(trades, current_balance, current_equity)

        daily_growth = [
            {"date": t.get("time"), "balance": t.get("balance")}
            for t in trades if "balance" in t
        ]

        return {
            "metrics": metrics,
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
    created_at = get_account_created_at(db, request.account_id)
    result = await fetch_stats(request.account_id, created_at)

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
