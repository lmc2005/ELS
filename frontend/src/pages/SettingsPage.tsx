import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

const ASR_OPTIONS = [
  { value: 'qwen3_asr_mlx', label: 'Qwen3-ASR-0.6B (MLX)' },
  { value: 'whisper_base_en', label: 'Whisper Base.en' },
  { value: 'whisper_small_en', label: 'Whisper Small.en' },
];

const TTS_PROVIDER_OPTIONS = [
  { value: 'kokoro', label: 'Kokoro British Voice (low latency)' },
  { value: 'qwen3_tts_mlx', label: 'Qwen3-TTS MLX' },
  { value: 'qwen3_tts_torch', label: 'Qwen3-TTS PyTorch' },
  { value: 'macos_say', label: 'macOS Say' },
];

const KOKORO_VOICE_OPTIONS = [
  { value: 'bm_lewis', label: 'bm_lewis · British male · current' },
  { value: 'bf_lily', label: 'bf_lily · British female' },
  { value: 'bf_emma', label: 'bf_emma · British female' },
  { value: 'bf_alice', label: 'bf_alice · British female' },
  { value: 'bf_isabella', label: 'bf_isabella · British female' },
  { value: 'bm_george', label: 'bm_george · British male' },
  { value: 'bm_daniel', label: 'bm_daniel · British male' },
  { value: 'bm_fable', label: 'bm_fable · British male' },
];

export default function SettingsPage() {
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const updateMutation = useMutation({
    mutationFn: api.updateSettings,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const testMutation = useMutation({
    mutationFn: api.testLLM,
  });

  const [form, setForm] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<string | null>(null);

  if (isLoading) return <div>Loading...</div>;

  const current = settings ?? {};
  const valueOf = (key: string) => form[key] ?? current[key] ?? '';

  const handleChange = (key: string, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    updateMutation.mutate(form);
  };

  const handleTest = async () => {
    setTestResult(null);
    try {
      await testMutation.mutateAsync();
      setTestResult('Connection successful!');
    } catch (error: any) {
      setTestResult(`Connection failed: ${error.message}`);
    }
  };

  return (
    <div>
      <h1 className="page-title">Settings</h1>

      <div className="card" style={{ maxWidth: 560 }}>
        <div style={{ display: 'grid', gap: 14 }}>
          <div>
            <label>LLM Base URL</label>
            <input value={valueOf('llm_base_url')} onChange={(e) => handleChange('llm_base_url', e.target.value)} />
          </div>

          <div>
            <label>API Key</label>
            <input
              type="password"
              value={valueOf('llm_api_key')}
              onChange={(e) => handleChange('llm_api_key', e.target.value)}
            />
          </div>

          <div>
            <label>Model Name</label>
            <input value={valueOf('llm_model')} onChange={(e) => handleChange('llm_model', e.target.value)} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label>Input Price / 1K Tokens (RMB)</label>
              <input
                type="number"
                value={valueOf('input_price_per_1k_tokens_rmb')}
                onChange={(e) => handleChange('input_price_per_1k_tokens_rmb', e.target.value)}
              />
            </div>
            <div>
              <label>Output Price / 1K Tokens (RMB)</label>
              <input
                type="number"
                value={valueOf('output_price_per_1k_tokens_rmb')}
                onChange={(e) => handleChange('output_price_per_1k_tokens_rmb', e.target.value)}
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label>Monthly Budget (RMB)</label>
              <input
                type="number"
                value={valueOf('monthly_budget_rmb')}
                onChange={(e) => handleChange('monthly_budget_rmb', e.target.value)}
              />
            </div>
            <div>
              <label>Budget Warning (RMB)</label>
              <input
                type="number"
                value={valueOf('budget_warning_rmb')}
                onChange={(e) => handleChange('budget_warning_rmb', e.target.value)}
              />
            </div>
          </div>

          <div>
            <label>ASR Model</label>
            <select value={valueOf('asr_model')} onChange={(e) => handleChange('asr_model', e.target.value)}>
              {ASR_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label>TTS Provider</label>
            <select value={valueOf('tts_provider')} onChange={(e) => handleChange('tts_provider', e.target.value)}>
              {TTS_PROVIDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label>TTS Model</label>
            <input value={valueOf('tts_model')} onChange={(e) => handleChange('tts_model', e.target.value)} />
          </div>

          <div>
            <label>TTS Voice</label>
            <select value={valueOf('tts_voice')} onChange={(e) => handleChange('tts_voice', e.target.value)}>
              {KOKORO_VOICE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
          <button onClick={handleSave} disabled={updateMutation.isPending}>
            {updateMutation.isPending ? 'Saving...' : 'Save Settings'}
          </button>
          <button className="secondary" onClick={handleTest} disabled={testMutation.isPending}>
            {testMutation.isPending ? 'Testing...' : 'Test LLM Connection'}
          </button>
        </div>

        {testResult && (
          <div
            style={{
              marginTop: 12,
              padding: '8px 12px',
              borderRadius: 6,
              background: testResult.includes('successful') ? 'rgba(85,192,144,0.15)' : 'rgba(224,85,106,0.15)',
              color: testResult.includes('successful') ? 'var(--success)' : 'var(--danger)',
              fontSize: 13,
            }}
          >
            {testResult}
          </div>
        )}

        {updateMutation.isSuccess && (
          <div style={{ marginTop: 12, color: 'var(--success)', fontSize: 13 }}>Settings saved.</div>
        )}
      </div>
    </div>
  );
}
