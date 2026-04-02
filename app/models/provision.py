from pydantic import BaseModel, field_validator


class MT5Credentials(BaseModel):
    user_id: int
    name: str
    login: str
    password: str
    server: str
    platform: str = "mt5"
    magic: int = 0
    risk_level: str
    metastats_enabled: bool = True
    pool_id: str | None = None

    @field_validator("login")
    @classmethod
    def login_digits_only(cls, v):
        if not v.isdigit():
            raise ValueError("login should consist of digits only")
        return v
