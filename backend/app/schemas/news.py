from pydantic import BaseModel, ConfigDict


class NewsArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    title: str
    source: str
    url: str
    image_url: str | None = None
    published_at: str | None = None
    summary: str | None = None
    keywords_json: str | None = None
    difficulty: str


class NewsInteractionCreate(BaseModel):
    article_id: int
    action: str  # open | stay_60s | bookmark | skip
