const BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

async function upload<T>(url: string, formData: FormData): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Settings
  getSettings: () => request<any>('/settings'),
  updateSettings: (data: any) => request<any>('/settings', { method: 'PUT', body: JSON.stringify(data) }),
  testLLM: () => request<any>('/settings/test-llm', { method: 'POST' }),

  // Games
  getGameProfile: () => request<any>('/games/profile'),
  getGamesHistory: () => request<any>('/games/history'),
  purchaseUpgrade: (data: any) => request<any>('/games/shop/purchase', { method: 'POST', body: JSON.stringify(data) }),
  startInterrogation: (data?: any) => request<any>('/games/interrogation/start', { method: 'POST', body: JSON.stringify(data ?? {}) }),

  // Speaking
  createSession: (data: any) => request<any>('/speaking/sessions', { method: 'POST', body: JSON.stringify(data) }),
  getSession: (id: number) => request<any>(`/speaking/sessions/${id}`),
  endSession: (id: number) => request<any>(`/speaking/sessions/${id}/end`, { method: 'POST' }),

  // Diary
  createDiary: (data: any) => request<any>('/diary', { method: 'POST', body: JSON.stringify(data) }),
  listDiaries: () => request<any[]>('/diary'),
  getDiary: (id: number) => request<any>(`/diary/${id}`),

  // News
  getNews: (category = 'ai_tech') => request<any[]>(`/news?category=${category}`),
  refreshNews: () => request<any>('/news/refresh', { method: 'POST' }),
  recordInteraction: (data: any) => request<any>('/news/interactions', { method: 'POST', body: JSON.stringify(data) }),

  // Vocab
  getVocabImages: (word: string) => request<any>(`/vocab/images?word=${encodeURIComponent(word)}`),

  // Story
  startStory: (data?: any) => request<any>('/story/start', { method: 'POST', body: JSON.stringify(data ?? {}) }),
  submitAttempt: (storyId: number, data: any) => request<any>(`/story/${storyId}/attempt`, { method: 'POST', body: JSON.stringify(data) }),
  submitAttemptAudio: (storyId: number, audio: Blob) => {
    const formData = new FormData();
    formData.append('audio', audio, `story-${storyId}-attempt.webm`);
    return upload<any>(`/story/${storyId}/attempt-audio`, formData);
  },
  getProgress: () => request<any>('/story/progress'),
  getLevels: () => request<any[]>('/story/levels'),

  // History
  getHistory: () => request<any[]>('/history/speaking'),
  getDiaryHistory: () => request<any[]>('/history/diary'),
  getVocabHistory: () => request<any[]>('/history/vocab'),
  deleteHistorySession: (id: number) => request<any>(`/history/speaking/${id}`, { method: 'DELETE' }),
  deleteDiaryHistory: (id: number) => request<any>(`/history/diary/${id}`, { method: 'DELETE' }),
  deleteVocabHistory: (id: number) => request<any>(`/history/vocab/${id}`, { method: 'DELETE' }),
};
