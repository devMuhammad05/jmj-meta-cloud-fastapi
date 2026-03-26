from pydantic import BaseModel


class StatsRequest(BaseModel):
    account_id: str  # MetaApi account UUID
