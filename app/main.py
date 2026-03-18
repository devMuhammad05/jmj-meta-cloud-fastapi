from fastapi import FastAPI
from app.routers import provison_account, trading_stats

app = FastAPI()

app.include_router(provison_account.router)
app.include_router(trading_stats.router)


@app.get("/")
def root():
    return {"message": "FastAPI is running"}
