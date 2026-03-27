from fastapi import FastAPI
from sqlalchemy import text
from app.routers import provison_account, trading_stats
from app.database import engine

app = FastAPI()

app.include_router(provison_account.router)
app.include_router(trading_stats.router)


@app.get("/")
def root():
    return {"message": "FastAPI is running"}


@app.get("/api/health/db")
def db_health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "message": "Database connection successful"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
