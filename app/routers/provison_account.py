from fastapi import HTTPException, Depends, APIRouter
from app.models.provision import ProvisionRequest
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
# Fetch and validate credentials from DB
# ------------------------
def get_credentials_from_db(db: Session, meta_trader_credential_id: int, user_id: int) -> dict:
    result = db.execute(
        text("""
        SELECT id, user_id, mt_account_number, mt_password, mt_server, platform_type, risk_level
        FROM meta_trader_credentials
        WHERE id = :meta_trader_credential_id
        """),
        {"meta_trader_credential_id": meta_trader_credential_id}
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Credentials not found")

    if result.user_id != user_id:
        raise HTTPException(status_code=403, detail="Credentials do not belong to this user")

    return {
        "id": result.id,
        "user_id": result.user_id,
        "login": result.mt_account_number,
        "password": result.mt_password,
        "server": result.mt_server,
        "platform": result.platform_type,
        "risk_level": result.risk_level,
    }


# ------------------------
# Update credentials record with provisioning results
# ------------------------
def update_credentials_in_db(db: Session, meta_trader_credential_id: int, account_id: str, initial_deposit: float):
    try:
        query = text("""
        UPDATE meta_trader_credentials
        SET account_id = :account_id,
            initial_deposit = :initial_deposit,
            status = 'connected',
            updated_at = NOW()
        WHERE id = :meta_trader_credential_id
        """)
        db.execute(
            query,
            {
                "account_id": account_id,
                "initial_deposit": initial_deposit,
                "meta_trader_credential_id": meta_trader_credential_id,
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
async def provision_account(payload: ProvisionRequest, db: Session):
    token = os.getenv("META_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MetaApi token not set in environment")

    # Fetch and validate credentials before entering MetaAPI try/except
    creds = get_credentials_from_db(db, payload.meta_trader_credential_id, payload.user_id)

    api = MetaApi(token)
    connection = None
    account = None

    try:
        # Check if account already exists
        accounts = await api.metatrader_account_api.get_accounts_with_infinite_scroll_pagination()
        account = next(
            (a for a in accounts if a.login == creds["login"] and a.server == creds["server"]),
            None
        )

        if account:
            print(f"Account already exists! MetaApi ID: {account.id}")
        else:
            # Create MT5 account — use mt_account_number as name
            account_data = {
                "name": creds["login"],
                "type": "cloud",
                "login": creds["login"],
                "password": creds["password"],
                "server": creds["server"],
                "platform": creds["platform"],
                "magic": 0,
                "application": "MetaApi",
                "metastatsApiEnabled": True,
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

        # Update existing record with provisioning results
        update_credentials_in_db(db, payload.meta_trader_credential_id, account.id, info.get("balance", 0.0))
        save_metric_to_db(db, account.id, info.get("balance", 0.0))

        return {"message": "Account provisioned successfully"}

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
# Endpoints
# ------------------------
@router.get("/api/meta/accounts")
async def list_meta_accounts():
    token = os.getenv("META_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MetaApi token not set in environment")

    api = MetaApi(token)
    try:
        accounts = await api.metatrader_account_api.get_accounts_with_infinite_scroll_pagination()
        return {
            "count": len(accounts),
            "accounts": [
                {
                    "id": a.id,
                    "name": a.name,
                    "login": a.login,
                    "server": a.server,
                    "platform": a.platform,
                    "state": a.state,
                    "connection_status": a.connection_status,
                }
                for a in accounts
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        api.close()


@router.post("/api/provision-account")
async def register_mt5(payload: ProvisionRequest, db: Session = Depends(get_db)):
    return await provision_account(payload, db)
