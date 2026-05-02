import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

type HistoryTab = 'speaking' | 'diary' | 'vocab';

export default function HistoryPage() {
  const [tab, setTab] = useState<HistoryTab>('speaking');
  const queryClient = useQueryClient();

  const { data: speakingHistory, isLoading: speakingLoading } = useQuery({
    queryKey: ['history-speaking'],
    queryFn: api.getHistory,
    enabled: tab === 'speaking',
  });

  const { data: diaryHistory, isLoading: diaryLoading } = useQuery({
    queryKey: ['history-diary'],
    queryFn: api.getDiaryHistory,
    enabled: tab === 'diary',
  });

  const { data: vocabHistory, isLoading: vocabLoading } = useQuery({
    queryKey: ['history-vocab'],
    queryFn: api.getVocabHistory,
    enabled: tab === 'vocab',
  });

  const deleteSpeaking = useMutation({
    mutationFn: api.deleteHistorySession,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['history-speaking'] }),
  });

  const deleteDiary = useMutation({
    mutationFn: api.deleteDiaryHistory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['history-diary'] }),
  });

  const deleteVocab = useMutation({
    mutationFn: api.deleteVocabHistory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['history-vocab'] }),
  });

  const tabs = [
    { key: 'speaking', label: 'Speaking' },
    { key: 'diary', label: 'Diary' },
    { key: 'vocab', label: 'Vocab' },
  ];

  const confirmDelete = (label: string, onConfirm: () => void) => {
    if (window.confirm(`Delete this ${label} record? This cannot be undone.`)) {
      onConfirm();
    }
  };

  return (
    <div className="page-stack">
      <section className="hero-band">
        <div>
          <div className="eyebrow">History</div>
          <h1 className="page-title">Review, replay, and clean up your learning trail</h1>
          <p className="page-subtitle">
            Everything is grouped by practice mode so you can revisit useful runs and remove the noisy ones.
          </p>
        </div>
      </section>

      <section className="surface">
        <div className="mode-switch">
          {tabs.map((item) => (
            <button
              key={item.key}
              type="button"
              className={tab === item.key ? '' : 'secondary'}
              onClick={() => setTab(item.key as HistoryTab)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>

      {tab === 'speaking' && (
        <section className="surface">
          <div className="section-head">
            <h2 className="section-title">Speaking sessions</h2>
            <span className="section-tag">{speakingHistory?.length || 0} sessions</span>
          </div>
          {speakingLoading ? <p className="empty-copy">Loading speaking history...</p> : null}
          {!speakingLoading && !speakingHistory?.length ? <p className="empty-copy">No speaking sessions yet.</p> : null}
          <div className="stack-list">
            {speakingHistory?.map((session: any) => (
              <div key={session.id} className="history-row">
                <div className="history-header">
                  <div>
                    <div className="history-title">{session.topic || 'Open conversation'}</div>
                    <div className="history-meta">
                      {session.mode} · {session.coach_focus} · {session.started_at?.slice(0, 16)} · {session.utterance_count} turns
                    </div>
                  </div>
                  <div className="room-actions">
                    <span className="section-tag">¥{session.cost_rmb?.toFixed(4)}</span>
                    <button
                      className="danger"
                      onClick={() => confirmDelete('speaking session', () => deleteSpeaking.mutate(session.id))}
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {session.summary ? <div className="surface-copy" style={{ whiteSpace: 'pre-line' }}>{session.summary}</div> : null}
                {session.recording_path ? <audio controls src={session.recording_path} style={{ width: '100%' }} /> : null}

                {session.utterances?.length ? (
                  <div className="message-list compact">
                    {session.utterances.map((utterance: any, index: number) => (
                      <div key={index} className={`message-bubble ${utterance.role}`}>
                        <div className="message-role">{utterance.role === 'user' ? 'You' : 'Tutor'}</div>
                        <div>{utterance.text}</div>
                      </div>
                    ))}
                  </div>
                ) : null}

                {session.notes?.length ? (
                  <div className="stack-list">
                    {session.notes.map((note: any, index: number) => (
                      <div key={index} className="note-chip">
                        <strong>{note.type}</strong>
                        <span>{note.content}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'diary' && (
        <section className="surface">
          <div className="section-head">
            <h2 className="section-title">Diary entries</h2>
            <span className="section-tag">{diaryHistory?.length || 0} entries</span>
          </div>
          {diaryLoading ? <p className="empty-copy">Loading diary history...</p> : null}
          {!diaryLoading && !diaryHistory?.length ? <p className="empty-copy">No diary entries yet.</p> : null}
          <div className="stack-list">
            {diaryHistory?.map((entry: any) => (
              <div key={entry.id} className="history-row">
                <div className="history-header">
                  <div>
                    <div className="history-title">{entry.date}</div>
                    <div className="history-meta">{entry.created_at?.slice(0, 16)}</div>
                  </div>
                  <button
                    className="danger"
                    onClick={() => confirmDelete('diary entry', () => deleteDiary.mutate(entry.id))}
                  >
                    Delete
                  </button>
                </div>
                <div className="transcript-box">{entry.original_text}</div>
                {entry.better_version ? (
                  <div className="teacher-box">
                    <div className="mini-heading">Polished version</div>
                    <p>{entry.better_version}</p>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'vocab' && (
        <section className="surface">
          <div className="section-head">
            <h2 className="section-title">Vocabulary lookups</h2>
            <span className="section-tag">{vocabHistory?.length || 0} searches</span>
          </div>
          {vocabLoading ? <p className="empty-copy">Loading vocabulary history...</p> : null}
          {!vocabLoading && !vocabHistory?.length ? <p className="empty-copy">No vocabulary searches yet.</p> : null}
          <div className="level-grid">
            {vocabHistory?.map((item: any) => (
              <div key={item.id} className="level-tile selected" style={{ alignItems: 'flex-start' }}>
                <div className="level-topline">
                  <span>{item.word}</span>
                  <button
                    className="danger"
                    style={{ padding: '6px 10px' }}
                    onClick={() => confirmDelete('vocabulary search', () => deleteVocab.mutate(item.id))}
                  >
                    Delete
                  </button>
                </div>
                <strong>{item.word}</strong>
                <span>{item.created_at?.slice(0, 16)}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
