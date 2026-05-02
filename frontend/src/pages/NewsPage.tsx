import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

function parseKeywords(value?: string | null): string[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export default function NewsPage() {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState('ai_tech');

  const { data: articles, isLoading } = useQuery({
    queryKey: ['news', category],
    queryFn: () => api.getNews(category),
  });

  const refreshMutation = useMutation({
    mutationFn: api.refreshNews,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['news'] }),
  });

  const featuredArticle = articles?.[0];
  const remainingArticles = articles?.slice(1) ?? [];

  return (
    <div className="page-stack">
      <section className="hero-band">
        <div>
          <div className="eyebrow">Daily News</div>
          <h1 className="page-title">Read fresh English news with richer context</h1>
          <p className="page-subtitle">
            AI tech and current affairs are sorted by recency plus your reading signals. Images are shown only when the feed or article provides a usable news image.
          </p>
        </div>
        <button
          className="secondary"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
        >
          {refreshMutation.isPending ? 'Fetching...' : 'Refresh'}
        </button>
      </section>

      <section className="surface">
        <div className="mode-switch">
          <button
            onClick={() => setCategory('ai_tech')}
            className={category === 'ai_tech' ? '' : 'secondary'}
          >
            AI Tech
          </button>
          <button
            onClick={() => setCategory('current_affairs')}
            className={category === 'current_affairs' ? '' : 'secondary'}
          >
            Current Affairs
          </button>
        </div>
      </section>

      {isLoading && <p className="empty-copy">Loading...</p>}

      {articles?.length === 0 && !isLoading && (
        <section className="surface">
          <p className="empty-copy">No articles yet. Click Refresh to fetch the latest news.</p>
        </section>
      )}

      {featuredArticle && (
        <a
          href={featuredArticle.url}
          target="_blank"
          rel="noopener noreferrer"
          className="surface feature-news-card"
          onClick={() => void api.recordInteraction({ article_id: featuredArticle.id, action: 'open' })}
          style={{ color: 'inherit', textDecoration: 'none' }}
        >
          <div className="feature-news-media">
            {featuredArticle.image_url ? (
              <img
                src={featuredArticle.image_url}
                alt={featuredArticle.title}
                loading="lazy"
                onError={(event) => {
                  (event.target as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <div className="feature-news-fallback">Daily lead</div>
            )}
          </div>
          <div className="feature-news-copy">
            <div className="history-meta" style={{ justifyContent: 'flex-start', marginBottom: 10 }}>
              <span>{featuredArticle.source}</span>
              {featuredArticle.published_at && <span>{featuredArticle.published_at}</span>}
              <span>{featuredArticle.difficulty}</span>
            </div>
            <h2 className="page-title" style={{ fontSize: 32 }}>{featuredArticle.title}</h2>
            {featuredArticle.summary && <p className="page-subtitle" style={{ marginTop: 14 }}>{featuredArticle.summary}</p>}
            <div className="mode-switch" style={{ marginTop: 14, marginBottom: 0 }}>
              {parseKeywords(featuredArticle.keywords_json).slice(0, 5).map((keyword) => (
                <span key={keyword} className="section-tag">{keyword}</span>
              ))}
            </div>
          </div>
        </a>
      )}

      <section className="news-card-grid">
        {remainingArticles.map((article: any) => {
          const keywords = parseKeywords(article.keywords_json);
          const handleOpen = () => {
            void api.recordInteraction({ article_id: article.id, action: 'open' });
          };
          return (
            <a
              key={article.id}
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="surface news-card"
              onClick={handleOpen}
              style={{ color: 'inherit', textDecoration: 'none' }}
            >
              {article.image_url ? (
                <img
                  src={article.image_url}
                  alt={article.title}
                  loading="lazy"
                  className="news-card-image"
                  onError={(event) => {
                    (event.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              ) : null}
              <div className="history-meta" style={{ justifyContent: 'flex-start', marginBottom: 8 }}>
                <span>{article.source}</span>
                {article.published_at && <span>{article.published_at}</span>}
              </div>
              <h2 className="section-title" style={{ marginBottom: 8 }}>{article.title}</h2>
              {article.summary && <p className="surface-copy" style={{ marginTop: 0 }}>{article.summary}</p>}
              {keywords.length > 0 && (
                <div className="mode-switch" style={{ marginTop: 12, marginBottom: 0 }}>
                  {keywords.slice(0, 4).map((keyword) => (
                    <span key={keyword} className="section-tag">{keyword}</span>
                  ))}
                </div>
              )}
            </a>
          );
        })}
      </section>
    </div>
  );
}
