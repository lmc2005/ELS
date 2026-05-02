import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

export default function DiaryPage() {
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [viewingId, setViewingId] = useState<number | null>(null);

  const { data: entries, isLoading } = useQuery({
    queryKey: ['diaries'],
    queryFn: api.listDiaries,
  });

  const createMutation = useMutation({
    mutationFn: api.createDiary,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diaries'] });
      setText('');
    },
  });

  const handleSubmit = () => {
    if (!text.trim()) return;
    createMutation.mutate({ text: text.trim() });
  };

  const viewing = entries?.find((e: any) => e.id === viewingId);
  let feedback: any = null;
  if (viewing?.feedback_json) {
    try { feedback = JSON.parse(viewing.feedback_json); } catch {}
  }

  return (
    <div style={{ display: 'flex', gap: 20, height: 'calc(100vh - 80px)' }}>
      {/* Write area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <h1 className="page-title">Diary</h1>
        <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Write your English diary here..."
            style={{ flex: 1, resize: 'none', minHeight: 200, lineHeight: 1.6 }}
          />
          <button
            onClick={handleSubmit}
            disabled={createMutation.isPending || !text.trim()}
            style={{ marginTop: 12, alignSelf: 'flex-start' }}
          >
            {createMutation.isPending ? 'Analyzing...' : 'Submit for Feedback'}
          </button>
          {createMutation.isError && (
            <p style={{ color: 'var(--danger)', fontSize: 13, marginTop: 8 }}>
              {(createMutation.error as Error).message}
            </p>
          )}
        </div>
      </div>

      {/* Feedback & History */}
      <div style={{ width: 360, display: 'flex', flexDirection: 'column', gap: 16, overflow: 'auto' }}>
        {viewing && feedback ? (
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <h3 style={{ fontSize: 14 }}>Feedback — {viewing.date}</h3>
              <button className="secondary" onClick={() => setViewingId(null)} style={{ fontSize: 12, padding: '4px 8px' }}>
                Back
              </button>
            </div>

            {feedback.better_version && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>Polished Version</div>
                <div style={{ fontSize: 13, lineHeight: 1.5, padding: '8px 10px', background: 'var(--bg)', borderRadius: 6 }}>
                  {feedback.better_version}
                </div>
              </div>
            )}

            {feedback.grammar_issues?.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>Grammar Issues</div>
                {feedback.grammar_issues.map((gi: any, i: number) => (
                  <div key={i} style={{ fontSize: 13, marginBottom: 8 }}>
                    <div style={{ color: 'var(--danger)', textDecoration: 'line-through' }}>{gi.original}</div>
                    <div style={{ color: 'var(--success)' }}>{gi.corrected}</div>
                    <div style={{ color: 'var(--text-dim)', fontSize: 11 }}>{gi.explanation_zh}</div>
                  </div>
                ))}
              </div>
            )}

            {feedback.sentence_upgrades?.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>Sentence Upgrades</div>
                {feedback.sentence_upgrades.map((su: any, i: number) => (
                  <div key={i} style={{ fontSize: 13, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-dim)' }}>{su.original}</span>
                    {' → '}
                    <span style={{ color: 'var(--success)' }}>{su.upgraded}</span>
                  </div>
                ))}
              </div>
            )}

            {feedback.useful_phrases?.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>Useful Phrases</div>
                {feedback.useful_phrases.map((p: string, i: number) => (
                  <span key={i} style={{
                    display: 'inline-block', margin: '2px 4px 2px 0', padding: '2px 8px',
                    fontSize: 12, background: 'rgba(124,140,224,0.15)', color: 'var(--accent)',
                    borderRadius: 4,
                  }}>{p}</span>
                ))}
              </div>
            )}

            {feedback.overall_advice && (
              <div style={{ fontSize: 13, color: 'var(--text-dim)', borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                {feedback.overall_advice}
              </div>
            )}
          </div>
        ) : (
          <div className="card">
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>History</h3>
            {isLoading && <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>Loading...</p>}
            {entries?.length === 0 && <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>No entries yet</p>}
            {entries?.map((e: any) => (
              <div
                key={e.id}
                onClick={() => setViewingId(e.id)}
                style={{
                  padding: '8px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 6,
                  background: viewingId === e.id ? 'rgba(124,140,224,0.1)' : 'transparent',
                  border: '1px solid var(--border)',
                }}
              >
                <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>{e.date}</div>
                <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {e.original_text.slice(0, 80)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
