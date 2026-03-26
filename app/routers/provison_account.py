from fastapi import HTTPException, Depends, APIRouter
from app.models.provision import MT5Credentials
from sqlalchemy.orm import Session
from app.database import get_db
import os
import logging
from metaapi_cloud_sdk import MetaApi
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()
logging.getLogger('metaapi_cloud_sdk').setLevel(logging.WARNING)

router = APIRouter(
    tags=['Provision Account']
)


# ------------------------
# Save credentials to DB
# ------------------------
def save_to_db(db: Session, data: dict, account_info: dict, account_id: str):
    try:
        query = text("""
        INSERT INTO meta_trader_credentials
        (user_id, mt_account_number, mt_password, mt_server, platform_type, initial_deposit, risk_level, account_id, created_at, updated_at)
        VALUES (:user_id, :login, :password, :server, :platform, :balance, :risk_level, :account_id, NOW(), NOW())
        RETURNING id
        """)

        db.execute(
            query,
            {
                "user_id": data['user_id'],
                "login": data['login'],
                "password": data['password'],
                "server": data['server'],
                "platform": data.get('platform', 'mt5'),
                "balance": account_info.get('balance', 0.0),
                "risk_level": data['risk_level'],
                "account_id": account_id,
            }
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise e


# ------------------------
# Save initial metric record (only balance on first insert)
# ------------------------
def save_metric_to_db(db: Session, account_id: str, balance: float):
    try:
        query = text("""
        INSERT INTO meta_account_metrics (account_id, balance, created_at, updated_at)
        VALUES (:account_id, :balance, NOW(), NOW())
        """)
        db.execute(query, {"account_id": account_id, "balance": balance})
        db.commit()
    except Exception as e:
        db.rollback()
        raise e


# ------------------------
# MetaApi provisioning logic
# ------------------------
async def provision_account(payload: MT5Credentials, db: Session):
    token = os.getenv("META_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MetaApi token not set in environment")

    api = MetaApi(token)
    connection = None
    account = None

    try:
        # Check if account already exists
        accounts = await api.metatrader_account_api.get_accounts_with_infinite_scroll_pagination()
        account = next(
            (a for a in accounts if a.login == payload.login and a.server == payload.server),
            None
        )

        if account:
            print(f"Account already exists! MetaApi ID: {account.id}")
        else:
            # Create MT5 account
            account_data = {
                "name": payload.name,
                "type": "cloud",
                "login": payload.login,
                "password": payload.password,
                "server": payload.server,
                "platform": payload.platform,
                "magic": payload.magic,
                "application": "MetaApi",
                "metastatsApiEnabled": payload.metastats_enabled,
            }
            account = await api.metatrader_account_api.create_account(account_data)
            print(f"Account registered! MetaApi ID: {account.id}")

        # Deploy if not already
        if account.state not in ("DEPLOYED", "DEPLOYING"):
            await account.deploy()
        await account.wait_deployed(timeout_in_seconds=120)

        # Connect and fetch account info
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized(timeout_in_seconds=120)
        info = await connection.get_account_information()
        print(f"Connected! Balance: {info['balance']} {info['currency']}")

        # Save to DB
        save_to_db(db, payload.dict(), info, account.id)
        save_metric_to_db(db, account.id, info.get("balance", 0.0))

        return {
            "message": "Account provisioned successfully",
            "account_id": account.id,
            "balance": info["balance"],
            "equity": info["equity"],
            "leverage": info["leverage"],
        }

    except Exception as e:
        detail = {"message": str(e)}
        if hasattr(e, "details"):
            detail["details"] = e.details
        raise HTTPException(status_code=400, detail=detail)
    finally:
        if connection:
            try:
                await connection.close()
            except KeyError:
                pass
            except Exception as e:
                print("Error closing connection:", e)
        api.close()


# ------------------------
# Endpoint
# ------------------------
@router.post("/api/provision-account")
async def register_mt5(payload: MT5Credentials, db: Session = Depends(get_db)):
    return await provision_account(payload, db)
