from sqlmodel import SQLModel, Field


class SettingsModel(SQLModel, table=True):
    __tablename__ = "settings"

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(default="", unique=True, index=True)
    value: str = Field(default="")
