import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

interface VocabExample {
  sentence: string;
  note?: string;
}

export default function VocabImagePage() {
  const [word, setWord] = useState('');
  const [searchWord, setSearchWord] = useState('');

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ['vocab', searchWord],
    queryFn: () => api.getVocabImages(searchWord),
    enabled: searchWord.length > 0,
  });

  const handleSearch = () => {
    if (word.trim()) setSearchWord(word.trim());
  };

  const results = data?.results ?? [];
  const examples: VocabExample[] = data?.examples ?? [];
  const isPhrase = searchWord.trim().includes(' ');
  const visibleResults = results.filter((item: any) => {
    const threshold = isPhrase ? 12 : 7;
    return (item.thumbnail_url || item.image_url) && Number(item.relevance_score || 0) >= threshold;
  });

  return (
    <div className="page-stack">
      <section className="hero-band">
        <div>
          <div className="eyebrow">Vocabulary Lens</div>
          <h1 className="page-title">Learn a word through meaning, examples, and images</h1>
          <p className="page-subtitle">
            Search a word or phrase. Concrete words can show vetted image results; abstract phrases stay focused on useful English sentences.
          </p>
        </div>
      </section>

      <section className="surface">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <input
            type="text"
            value={word}
            onChange={(event) => setWord(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && handleSearch()}
            placeholder="apple, take off, put off..."
            style={{ flex: 1 }}
          />
          <button onClick={handleSearch} disabled={isFetching}>
            {isFetching ? 'Searching...' : 'Search'}
          </button>
        </div>
        <div className="status-strip" style={{ marginTop: 14 }}>
          <div className="status-pill quiet">{isPhrase ? 'Phrase mode' : 'Word mode'}</div>
          <div className="status-pill quiet">Sentence-first meaning</div>
          <div className="status-pill quiet">{visibleResults.length > 0 ? `${visibleResults.length} vetted visuals` : 'Visuals only when relevant'}</div>
        </div>
      </section>

      {isError && (
        <div className="error-banner">
          {(error as Error).message}
        </div>
      )}

      {data && (
        <section className="setup-grid">
          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">{data.word}</h2>
              <span className="section-tag">{data.part_of_speech || 'vocabulary'}</span>
            </div>
            <div className="teacher-box">
              <div className="mini-heading">Meaning</div>
              <p>{data.definition}</p>
            </div>
            {data.usage_note && (
              <p className="surface-copy">{data.usage_note}</p>
            )}
            <div className="retell-bounty-grid" style={{ marginTop: 16 }}>
              <div className="feedback-row mission-card">
                <div className="feedback-reason">Learning lens</div>
                <div className="feedback-after">{isPhrase ? 'Phrases need context, not literal pictures.' : 'Concrete nouns benefit from a clean visual anchor.'}</div>
              </div>
              <div className="feedback-row mission-card">
                <div className="feedback-reason">Result policy</div>
                <div className="feedback-after">{visibleResults.length > 0 ? 'Only relevance-checked images survived the filter.' : 'No visual passed the relevance threshold, so this stays text-first.'}</div>
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Example sentences</h2>
              <span className="section-tag">{examples.length}</span>
            </div>
            <div className="stack-list">
              {examples.map((item, index) => (
                <div key={index} className="feedback-row">
                  <div className="feedback-after">{item.sentence}</div>
                  {item.note && <div className="feedback-reason">{item.note}</div>}
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {searchWord && !isFetching && data && visibleResults.length === 0 && (
        <section className="surface">
          <p className="empty-copy">
            No image passed the relevance check for "{searchWord}", so this result is sentence-only.
          </p>
        </section>
      )}

      {visibleResults.length > 0 && (
        <section className="surface">
          <div className="section-head">
            <h2 className="section-title">Relevant images</h2>
            <span className="section-tag">{visibleResults.length} vetted</span>
          </div>
          <div className="news-card-grid">
            {visibleResults.map((item: any, index: number) => (
              <div key={index} className="feedback-row vocab-image-card" style={{ padding: 0, overflow: 'hidden' }}>
                <img
                  src={item.thumbnail_url || item.image_url}
                  alt={item.description || searchWord}
                  loading="lazy"
                  style={{ width: '100%', height: 180, objectFit: 'cover', display: 'block' }}
                  onError={(event) => {
                    (event.target as HTMLImageElement).style.display = 'none';
                  }}
                />
                <div style={{ padding: 12 }}>
                  <div className="history-meta" style={{ justifyContent: 'flex-start', marginBottom: 8 }}>
                    <span>{item.source}</span>
                    <span>{item.license}</span>
                    <span>relevance {item.relevance_score}</span>
                  </div>
                  {item.description && (
                    <div className="feedback-after" style={{ fontSize: 12, marginTop: 5, maxHeight: 42, overflow: 'hidden' }}>
                      {item.description}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
