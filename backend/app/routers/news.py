import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.schemas.news import NewsArticleOut, NewsInteractionCreate
from app.models.news import NewsArticle, NewsInteraction
from app.services.news_service import news_service

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("", response_model=list[NewsArticleOut])
def get_news(category: str = "ai_tech", s: Session = Depends(get_session)):
    articles = s.exec(
        select(NewsArticle).where(NewsArticle.category == category)
        .order_by(NewsArticle.published_at.desc()).limit(20)
    ).all()
    interactions = s.exec(select(NewsInteraction)).all()
    interaction_scores: dict[int, int] = {}
    for item in interactions:
        interaction_scores[item.article_id] = interaction_scores.get(item.article_id, 0) + item.weight
    articles = sorted(
        articles,
        key=lambda article: (interaction_scores.get(article.id or 0, 0), article.published_at or ""),
        reverse=True,
    )
    return [NewsArticleOut.model_validate(a) for a in articles]


@router.post("/refresh", response_model=dict)
async def refresh_news(s: Session = Depends(get_session)):
    results = await news_service.fetch_all()
    saved = 0
    for category, articles in results.items():
        for article in articles:
            # Check duplicate
            existing = s.exec(
                select(NewsArticle).where(NewsArticle.url == article["url"])
            ).first()
            if existing:
                for key in ("source", "image_url", "published_at", "summary", "keywords_json", "difficulty"):
                    if not getattr(existing, key, None) and article.get(key):
                        setattr(existing, key, article[key])
                continue
            na = NewsArticle(**article)
            s.add(na)
            saved += 1
    s.commit()
    return {"saved": saved, "categories": {k: len(v) for k, v in results.items()}}


@router.post("/interactions")
def record_interaction(body: NewsInteractionCreate, s: Session = Depends(get_session)):
    article = s.get(NewsArticle, body.article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    weights = {"open": 1, "stay_60s": 2, "bookmark": 3, "skip": -1}
    interaction = NewsInteraction(
        article_id=body.article_id,
        action=body.action,
        weight=weights.get(body.action, 0),
        created_at=datetime.utcnow().isoformat(),
    )
    s.add(interaction)
    s.commit()
    return {"status": "recorded", "article_id": body.article_id, "action": body.action}
