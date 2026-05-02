from datetime import datetime
from sqlmodel import SQLModel, Field


class NewsArticle(SQLModel, table=True):
    __tablename__ = "news_articles"

    id: int | None = Field(default=None, primary_key=True)
    category: str = Field(default="ai_tech")  # ai_tech | current_affairs
    title: str = Field(default="")
    source: str = Field(default="")
    url: str = Field(default="")
    image_url: str | None = Field(default=None)
    published_at: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    keywords_json: str | None = Field(default=None)  # JSON array string
    difficulty: str = Field(default="intermediate")  # beginner | intermediate | advanced


class NewsInteraction(SQLModel, table=True):
    __tablename__ = "news_interactions"

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="news_articles.id", index=True)
    action: str = Field(default="open")  # open | stay_60s | bookmark | skip
    weight: int = Field(default=0)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
