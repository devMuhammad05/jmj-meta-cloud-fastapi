from pydantic import BaseModel


class StatsRequest(BaseModel):
    account_id: str  # MetaApi account UUID
    days: int = 90   # Number of historical days to fetch
