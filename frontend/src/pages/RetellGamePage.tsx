import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import ImmersiveStageCanvas from '../components/ImmersiveStageCanvas';
import { getStoredAudioDeviceId, openPreferredAudioStream } from '../utils/audioInput';

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: any) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

declare global {
  interface Window {
    SpeechRecognition?: new () => BrowserSpeechRecognition;
    webkitSpeechRecognition?: new () => BrowserSpeechRecognition;
  }
}

type StoryMode = 'local_story' | 'web_story';
type Phase = 'idle' | 'listening' | 'countdown' | 'retelling' | 'scored';

interface StoryPayload {
  story_id: number;
  level: number;
  title: string;
  audio_path: string;
  story_text: string;
  story_mode: StoryMode;
  source_name: string;
  source_url: string;
  difficulty_tags: string[];
}

interface ScorePayload {
  story_id: number;
  transcript: string;
  total_score: number;
  passed: boolean;
  scores: Record<string, number>;
  key_points_covered: string[];
  key_points_missed: string[];
  advice: string;
  advice_audio_path: string;
  missed_points: string[];
  language_feedback: string[];
  next_tip: string;
}

interface LevelPayload {
  level: number;
  label: string;
  description: string;
  unlocked: boolean;
  completed: boolean;
  attempts: number;
  best_score: number;
}

interface ProgressPayload {
  current_level: number;
  highest_unlocked: number;
  total_attempts: number;
  cleared_levels: number;
  win_streak: number;
  average_score: number;
  attempts: ScorePayload[];
}

const RETELL_LIMIT_SECONDS = 90;
const RANK_TITLES = [
  'Fresh Listener',
  'Plot Tracker',
  'Detail Hunter',
  'Sequence Runner',
  'Memory Builder',
  'Twist Catcher',
  'Scene Weaver',
  'Recall Captain',
  'Story Closer',
  'Retell Legend',
];

export default function RetellGamePage() {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>('idle');
  const [storyMode, setStoryMode] = useState<StoryMode>('local_story');
  const [selectedLevel, setSelectedLevel] = useState(1);
  const [story, setStory] = useState<StoryPayload | null>(null);
  const [countdown, setCountdown] = useState(12);
  const [score, setScore] = useState<ScorePayload | null>(null);
  const [transcript, setTranscript] = useState('');
  const [note, setNote] = useState('');
  const [recordingError, setRecordingError] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [retellRemaining, setRetellRemaining] = useState(RETELL_LIMIT_SECONDS);
  const [liveTranscriptStatus, setLiveTranscriptStatus] = useState('Live transcript will appear here when supported.');

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const retellChunksRef = useRef<Blob[]>([]);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const liveRecognitionWantedRef = useRef(false);
  const liveTranscriptRef = useRef('');
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retellTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const feedbackAudioRef = useRef<HTMLAudioElement | null>(null);
  const storyAudioRef = useRef<HTMLAudioElement | null>(null);

  const { data: levels } = useQuery<LevelPayload[]>({
    queryKey: ['story-levels'],
    queryFn: api.getLevels,
  });

  const { data: progress } = useQuery<ProgressPayload>({
    queryKey: ['story-progress'],
    queryFn: api.getProgress,
  });

  useEffect(() => {
    if (!levels?.length) return;
    const preferred = levels.find((level) => level.level === selectedLevel && level.unlocked)
      ? selectedLevel
      : progress?.highest_unlocked || levels.find((level) => level.unlocked)?.level || 1;
    setSelectedLevel(preferred);
  }, [levels, progress?.highest_unlocked]);

  useEffect(() => {
    return () => {
      stopAllAudio();
      stopRetellRecorder();
      stopLiveRecognition();
      if (countdownRef.current) clearInterval(countdownRef.current);
      if (retellTimerRef.current) clearInterval(retellTimerRef.current);
    };
  }, []);

  const selectedLevelMeta = useMemo(
    () => levels?.find((level) => level.level === selectedLevel) || null,
    [levels, selectedLevel],
  );

  const applyScore = (data: ScorePayload) => {
    setScore(data);
    setTranscript(data.transcript || '');
    setPhase('scored');
    queryClient.invalidateQueries({ queryKey: ['story-levels'] });
    queryClient.invalidateQueries({ queryKey: ['story-progress'] });
  };

  const startMutation = useMutation({
    mutationFn: () => api.startStory({ mode: storyMode, level: selectedLevel }),
    onSuccess: (data: StoryPayload) => {
      stopAllAudio();
      setStory(data);
      setPhase('listening');
      setTranscript('');
      setScore(null);
      setNote('');
      setAudioError(null);
      setRecordingError(null);
      setRetellRemaining(RETELL_LIMIT_SECONDS);
    },
    onError: (error: any) => {
      setAudioError(error?.message || 'Could not prepare this story right now.');
    },
  });

  const submitMutation = useMutation({
    mutationFn: (data: { story_id: number; transcript: string }) =>
      api.submitAttempt(data.story_id, { transcript: data.transcript, story_id: data.story_id }),
    onSuccess: (data: ScorePayload) => applyScore(data),
  });

  const submitAudioMutation = useMutation({
    mutationFn: (data: { story_id: number; audio: Blob }) =>
      api.submitAttemptAudio(data.story_id, data.audio),
    onSuccess: (data: ScorePayload) => applyScore(data),
  });

  const stopAllAudio = () => {
    if (storyAudioRef.current) {
      storyAudioRef.current.pause();
      storyAudioRef.current = null;
    }
    if (feedbackAudioRef.current) {
      feedbackAudioRef.current.pause();
      feedbackAudioRef.current = null;
    }
  };

  const playFeedbackAudio = (audioPath: string) => {
    if (!audioPath) return;
    if (feedbackAudioRef.current) {
      feedbackAudioRef.current.pause();
      feedbackAudioRef.current = null;
    }
    const audio = new Audio(audioPath);
    feedbackAudioRef.current = audio;
    audio.onended = () => {
      feedbackAudioRef.current = null;
    };
    audio.play().catch(() => {
      feedbackAudioRef.current = null;
    });
  };

  const startCountdown = () => {
    if (countdownRef.current) clearInterval(countdownRef.current);
    setPhase('countdown');
    setCountdown(12);
    countdownRef.current = setInterval(() => {
      setCountdown((previous) => {
        if (previous <= 1) {
          if (countdownRef.current) clearInterval(countdownRef.current);
          void startRetelling();
          return 0;
        }
        return previous - 1;
      });
    }, 1000);
  };

  const startRetelling = async () => {
    setPhase('retelling');
    setRecordingError(null);
    setRetellRemaining(RETELL_LIMIT_SECONDS);
    setTranscript('');
    liveTranscriptRef.current = '';
    startLiveRecognition();

    if (retellTimerRef.current) clearInterval(retellTimerRef.current);
    retellTimerRef.current = setInterval(() => {
      setRetellRemaining((previous) => Math.max(0, previous - 1));
    }, 1000);

    try {
      const stream = await openPreferredAudioStream(getStoredAudioDeviceId());
      mediaStreamRef.current = stream;
      const recorderOptions = pickRecorderOptions();
      const recorder = recorderOptions ? new MediaRecorder(stream, recorderOptions) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      retellChunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) retellChunksRef.current.push(event.data);
      };

      recorder.start(250);
      autoStopRef.current = setTimeout(() => {
        if (mediaRecorderRef.current?.state === 'recording') handleFinishRetelling();
      }, RETELL_LIMIT_SECONDS * 1000);
    } catch (error: any) {
      setRecordingError(error?.message || 'Microphone access was denied.');
    }
  };

  const stopRetellRecorder = () => {
    stopLiveRecognition();
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current);
      autoStopRef.current = null;
    }
    if (retellTimerRef.current) {
      clearInterval(retellTimerRef.current);
      retellTimerRef.current = null;
    }
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  };

  const handleSubmitTyped = () => {
    if (!story || !transcript.trim()) return;
    stopRetellRecorder();
    submitMutation.mutate({ story_id: story.story_id, transcript: transcript.trim() });
  };

  const handleFinishRetelling = () => {
    if (!story) return;
    stopLiveRecognition();
    const bestTranscript = getBestTranscript();

    const recorder = mediaRecorderRef.current;
    if (recorder?.state === 'recording') {
      recorder.onstop = () => {
        mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
        const blob = new Blob(retellChunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (hasUsableLiveTranscript(bestTranscript)) {
          submitMutation.mutate({ story_id: story.story_id, transcript: bestTranscript });
        } else if (blob.size > 0) {
          submitAudioMutation.mutate({ story_id: story.story_id, audio: blob });
        } else if (bestTranscript) {
          submitMutation.mutate({ story_id: story.story_id, transcript: bestTranscript });
        }
      };
      recorder.stop();
      return;
    }

    const blob = new Blob(retellChunksRef.current, { type: 'audio/webm' });
    if (hasUsableLiveTranscript(bestTranscript)) {
      submitMutation.mutate({ story_id: story.story_id, transcript: bestTranscript });
      return;
    }
    if (blob.size > 0) {
      submitAudioMutation.mutate({ story_id: story.story_id, audio: blob });
      return;
    }
    if (bestTranscript) {
      submitMutation.mutate({ story_id: story.story_id, transcript: bestTranscript });
    }
  };

  const handleReset = () => {
    stopRetellRecorder();
    stopLiveRecognition();
    stopAllAudio();
    if (countdownRef.current) clearInterval(countdownRef.current);
    setPhase('idle');
    setStory(null);
    setScore(null);
    setTranscript('');
    setNote('');
    setRecordingError(null);
    setAudioError(null);

    if (score?.passed && progress?.highest_unlocked && selectedLevel < progress.highest_unlocked) {
      setSelectedLevel(progress.highest_unlocked);
    }
  };

  function pickRecorderOptions(): MediaRecorderOptions | undefined {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/mp4',
      'audio/webm',
      'audio/ogg;codecs=opus',
    ];
    for (const mimeType of candidates) {
      if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported(mimeType)) {
        return { mimeType };
      }
    }
    return undefined;
  }

  const hasUsableLiveTranscript = (value: string) => value.trim().split(/\s+/).filter(Boolean).length >= 6;

  const getBestTranscript = () => {
    const typed = transcript.trim();
    const live = liveTranscriptRef.current.trim();
    return typed.split(/\s+/).filter(Boolean).length >= live.split(/\s+/).filter(Boolean).length
      ? typed
      : live;
  };

  const startLiveRecognition = () => {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      liveRecognitionWantedRef.current = false;
      setLiveTranscriptStatus('Live transcript is not supported in this browser, so Qwen ASR will run after recording.');
      return;
    }

    try {
      liveRecognitionWantedRef.current = true;
      const recognition = new Recognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = 'en-GB';
      recognition.onresult = (event: any) => {
        let finalText = liveTranscriptRef.current;
        let interimText = '';
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const text = event.results[i][0]?.transcript || '';
          if (event.results[i].isFinal) {
            finalText = `${finalText} ${text}`.replace(/\s+/g, ' ').trim();
          } else {
            interimText = `${interimText} ${text}`.replace(/\s+/g, ' ').trim();
          }
        }
        liveTranscriptRef.current = finalText;
        setTranscript(`${finalText} ${interimText}`.replace(/\s+/g, ' ').trim());
        setLiveTranscriptStatus('Live transcript is running.');
      };
      recognition.onerror = () => {
        setLiveTranscriptStatus('Live transcript paused; Qwen ASR will be used if needed.');
      };
      recognition.onend = () => {
        recognitionRef.current = null;
        if (liveRecognitionWantedRef.current) {
          window.setTimeout(() => {
            if (liveRecognitionWantedRef.current && !recognitionRef.current) startLiveRecognition();
          }, 250);
        }
      };
      recognition.start();
      recognitionRef.current = recognition;
      setLiveTranscriptStatus('Live transcript is listening.');
    } catch {
      liveRecognitionWantedRef.current = false;
      setLiveTranscriptStatus('Live transcript could not start; Qwen ASR will be used if needed.');
    }
  };

  const stopLiveRecognition = () => {
    liveRecognitionWantedRef.current = false;
    if (!recognitionRef.current) return;
    try {
      recognitionRef.current.stop();
    } catch {
      // noop
    }
    recognitionRef.current = null;
  };

  const highestUnlocked = progress?.highest_unlocked || 1;
  const rankTitle = RANK_TITLES[Math.max(0, Math.min(highestUnlocked - 1, RANK_TITLES.length - 1))];
  const streakBonus = Math.min(4, progress?.win_streak || 0);
  const rewardPoints = 120 + selectedLevel * 35 + streakBonus * 20;
  const rewardMultiplier = 1 + Math.min(0.75, (progress?.win_streak || 0) * 0.08);
  const boostedRewardPoints = Math.round(rewardPoints * rewardMultiplier);
  const rankProgress = Math.min(100, Math.round((((progress?.average_score || 0) + highestUnlocked * 6) / 160) * 100));
  const pressureLabel = selectedLevel >= 8 ? 'High pressure' : selectedLevel >= 5 ? 'Focused pressure' : 'Warm-up pace';
  const modeLabel = storyMode === 'local_story' ? 'Tutor Story' : 'British Web Story';
  const bossStage = selectedLevel % 5 === 0;
  const dailyMissions = useMemo(() => ([
    {
      id: 'streak',
      label: 'Keep the streak alive',
      copy: 'Clear three runs in a row to harden recall under pressure.',
      progress: Math.min(progress?.win_streak || 0, 3),
      target: 3,
      reward: '+40 pts',
    },
    {
      id: 'unlock',
      label: `Push Level ${selectedLevel}`,
      copy: bossStage ? 'Boss route: hold structure and details together.' : 'Clear the highlighted level to open the next route.',
      progress: selectedLevelMeta?.completed ? 1 : 0,
      target: 1,
      reward: `+${boostedRewardPoints} pts`,
    },
    {
      id: 'variety',
      label: storyMode === 'web_story' ? 'Catch one British web story' : 'Finish one tutor route',
      copy: storyMode === 'web_story'
        ? 'Train the ear with a natural British source clip.'
        : 'Lock in one tutor-made route cleanly for smoother repetition.',
      progress: progress?.total_attempts ? 1 : 0,
      target: 1,
      reward: '+25 pts',
    },
  ]), [bossStage, boostedRewardPoints, progress?.total_attempts, progress?.win_streak, selectedLevel, selectedLevelMeta?.completed, storyMode]);
  const completedMissionCount = dailyMissions.filter((mission) => mission.progress >= mission.target).length;
  const runProgressPercent = Math.round(((RETELL_LIMIT_SECONDS - retellRemaining) / RETELL_LIMIT_SECONDS) * 100);
  const missionPressure = bossStage ? 'Boss arena' : pressureLabel;
  const arenaEnergy = phase === 'retelling'
    ? Math.max(42, Math.min(100, 58 + runProgressPercent / 2))
    : phase === 'countdown'
      ? 72
      : phase === 'listening'
        ? 48 + selectedLevel * 4
        : score?.passed
          ? 88
          : 54;
  const transcriptWordCount = transcript.trim().split(/\s+/).filter(Boolean).length;
  const sequenceSignalCount = (transcript.match(/\b(first|then|after|before|later|finally|when|while|because|so|suddenly|meanwhile)\b/gi) || []).length;
  const detailSignalCount = (transcript.match(/\b(one|two|three|yesterday|today|station|street|park|shop|school|friend|teacher|umbrella|rain|bag|phone|ticket|small|large|red|blue)\b/gi) || []).length;
  const sentenceBursts = Math.max(
    transcript.split(/[.!?]/).map((part) => part.trim()).filter(Boolean).length,
    transcriptWordCount ? Math.ceil(transcriptWordCount / 14) : 0,
  );
  const retellLocks = [
    {
      label: 'Main idea lock',
      value: Math.min(100, transcriptWordCount === 0 ? 10 : 26 + transcriptWordCount * 1.22),
      copy: 'Open with the central event before chasing details.',
    },
    {
      label: 'Sequence lock',
      value: Math.min(100, 12 + sequenceSignalCount * 19 + Math.min(26, transcriptWordCount / 4)),
      copy: 'Time words and cause/effect keep the plot from collapsing.',
    },
    {
      label: 'Detail lock',
      value: Math.min(100, 10 + detailSignalCount * 18 + Math.min(22, transcriptWordCount / 6)),
      copy: 'One vivid detail is often what upgrades a pass into a strong run.',
    },
    {
      label: 'Fluency lock',
      value: Math.min(100, 12 + sentenceBursts * 17 + Math.min(24, transcriptWordCount / 5)),
      copy: 'Keep speaking in chunks instead of stopping to perfect every sentence.',
    },
  ];
  const arenaSystems = [
    {
      title: 'Memory loadout',
      value: bossStage ? 'Boss preset' : 'Balanced preset',
      copy: bossStage
        ? 'Main idea, turning point, and one detail all matter on the same run.'
        : 'Prioritise structure first, then spend attention on details.',
    },
    {
      title: 'Reward reactor',
      value: `+${boostedRewardPoints} pts`,
      copy: `Current streak multiplier is x${rewardMultiplier.toFixed(2)} and grows with clean clears.`,
    },
    {
      title: 'Pressure field',
      value: missionPressure,
      copy: storyMode === 'web_story'
        ? 'Web stories sharpen ear stamina before you speak back.'
        : 'Tutor stories keep the loop tighter for repeat attempts.',
    },
  ];
  const nextLevelMeta = levels?.find((level) => level.level === Math.min(10, selectedLevel + 1)) || null;

  if (phase === 'idle') {
    return (
      <div className="page-stack">
        <section className="immersive-stage retell-arena-stage">
          <ImmersiveStageCanvas variant="retell" phase="idle" energy={arenaEnergy} secondary={selectedLevel * 8 + 20} />
          <div className="immersive-stage-overlay retell-stage-overlay">
            <div className="stage-kicker-group">
              <div className="eyebrow">Retell Challenge</div>
              <div className="status-strip">
                <div className="status-pill quiet">{rankTitle}</div>
                <div className="status-pill quiet">{modeLabel}</div>
                <div className="status-pill quiet">{missionPressure}</div>
                <div className="status-pill quiet">{bossStage ? 'Boss route armed' : `${completedMissionCount}/3 missions primed`}</div>
              </div>
            </div>
            <div className="retell-stage-copy">
              <h1 className="page-title">Enter a memory arena built for repeat play</h1>
              <p className="page-subtitle">
                Every run is a loop: listen once, hold the structure, retell under pressure, cash the reward, and unlock a harsher stage.
              </p>
            </div>
            <div className="retell-stage-hud">
              <div className="hud-pill">
                <span>Current rank</span>
                <strong>{rankTitle}</strong>
              </div>
              <div className="hud-pill">
                <span>Reward pressure</span>
                <strong>+{boostedRewardPoints} pts on clear</strong>
              </div>
              <div className="hud-pill">
                <span>Upgrade feel</span>
                <strong>{rankProgress}% toward the next title</strong>
              </div>
            </div>
          </div>
        </section>

        <section className="metrics-strip">
          <div className="metric-box">
            <span>Rank</span>
            <strong>{rankTitle}</strong>
          </div>
          <div className="metric-box">
            <span>Unlocked</span>
            <strong>Lv {highestUnlocked}</strong>
          </div>
          <div className="metric-box">
            <span>Clear reward</span>
            <strong>+{boostedRewardPoints} pts</strong>
          </div>
          <div className="metric-box">
            <span>Streak bonus</span>
            <strong>x{Math.max(1, streakBonus)}</strong>
          </div>
          <div className="metric-box">
            <span>Daily missions</span>
            <strong>{completedMissionCount}/3</strong>
          </div>
        </section>

        <section className="surface campaign-route-surface">
          <div className="section-head">
            <h2 className="section-title">Campaign route</h2>
            <span className="section-tag">{levels?.length || 0} arena stages</span>
          </div>
          <div className="campaign-route">
            {levels?.map((level) => {
              const bossNode = level.level % 5 === 0;
              return (
                <button
                  key={level.level}
                  type="button"
                  className={`campaign-node ${selectedLevel === level.level ? 'selected' : ''} ${level.completed ? 'cleared' : ''} ${bossNode ? 'boss' : ''}`}
                  disabled={!level.unlocked}
                  onClick={() => setSelectedLevel(level.level)}
                >
                  <div className="campaign-node-topline">
                    <span>Lv {level.level}</span>
                    <span>{bossNode ? 'Boss' : level.completed ? 'Cleared' : level.unlocked ? 'Ready' : 'Locked'}</span>
                  </div>
                  <strong>{level.label}</strong>
                  <p>{level.description}</p>
                  <div className="campaign-node-meta">
                    <span>{level.attempts} tries</span>
                    <span>Best {Math.round(level.best_score || 0)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="surface">
          <div className="section-head">
            <h2 className="section-title">Daily mission board</h2>
            <span className="section-tag">Short-loop rewards</span>
          </div>
          <div className="retell-bounty-grid">
            {dailyMissions.map((mission) => {
              const progressWidth = `${Math.max(8, Math.min(100, (mission.progress / mission.target) * 100))}%`;
              const complete = mission.progress >= mission.target;
              return (
                <div key={mission.id} className="feedback-row mission-card">
                  <div className="feedback-reason">{complete ? 'Completed' : mission.reward}</div>
                  <div className="feedback-after">{mission.label}</div>
                  <div className="surface-copy">{mission.copy}</div>
                  <div className="meter-track" style={{ marginTop: 12 }}>
                    <div className="meter-fill" style={{ width: progressWidth }} />
                  </div>
                  <div className="meter-caption">{mission.progress}/{mission.target}</div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="setup-grid retell-command-grid">
          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Choose your mission</h2>
              <span className="section-tag">{modeLabel}</span>
            </div>

            <div className="mode-switch">
              {[
                { value: 'local_story', label: 'Tutor story' },
                { value: 'web_story', label: 'British web story' },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={storyMode === option.value ? '' : 'secondary'}
                  onClick={() => setStoryMode(option.value as StoryMode)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <p className="surface-copy">
              {storyMode === 'local_story'
                ? 'This route uses the same low-latency British tutor voice as Speaking, so the game loop stays tight.'
                : 'This route pulls short British audio and keeps most stories around 45 to 90 seconds.'}
            </p>

            {selectedLevelMeta && (
              <div className="mission-hero quest-card">
                <div className="mission-number">Lv {selectedLevelMeta.level}</div>
                <div>
                  <div className="mission-title">{selectedLevelMeta.label}</div>
                  <div className="mission-desc">{selectedLevelMeta.description}</div>
                  <div className="surface-copy">Clear bonus: +{boostedRewardPoints} pts · Pressure: {bossStage ? 'Boss route' : pressureLabel}</div>
                </div>
                <div className="mission-stats">
                  <span>{selectedLevelMeta.attempts} attempts</span>
                  <span>Best {Math.round(selectedLevelMeta.best_score || 0)}</span>
                </div>
              </div>
            )}

            <div className="retell-bounty-grid">
              <div className="feedback-row">
                <div className="feedback-reason">Win condition</div>
                <div className="feedback-after">Hold the main idea, the turning point, and one precise detail.</div>
              </div>
              <div className="feedback-row">
                <div className="feedback-reason">Addiction loop</div>
                <div className="feedback-after">Every clear run lights up streaks, unlocks, and a larger reward multiplier.</div>
              </div>
            </div>

            <button onClick={() => startMutation.mutate()} disabled={startMutation.isPending}>
              {startMutation.isPending ? 'Preparing challenge...' : 'Start challenge'}
            </button>
            {audioError && <div className="error-banner" style={{ marginTop: 14 }}>{audioError}</div>}
          </div>

          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Arena systems</h2>
              <span className="section-tag">Run economy</span>
            </div>
            <div className="arena-system-grid">
              {arenaSystems.map((system) => (
                <div key={system.title} className="arena-system-card">
                  <span>{system.title}</span>
                  <strong>{system.value}</strong>
                  <p>{system.copy}</p>
                </div>
              ))}
            </div>
            {selectedLevelMeta && (
              <div className="selected-challenge-card">
                <div className="history-meta">
                  <span>Selected challenge</span>
                  <span>{bossStage ? 'Boss route' : 'Main route'}</span>
                </div>
                <strong>Lv {selectedLevelMeta.level} · {selectedLevelMeta.label}</strong>
                <p>{selectedLevelMeta.description}</p>
                <div className="meter-track" style={{ marginTop: 12 }}>
                  <div className="meter-fill" style={{ width: `${Math.max(12, Math.min(100, selectedLevelMeta.best_score || (selectedLevelMeta.unlocked ? 18 : 6)))}%` }} />
                </div>
                <div className="campaign-node-meta">
                  <span>{selectedLevelMeta.attempts} attempts</span>
                  <span>Best {Math.round(selectedLevelMeta.best_score || 0)}</span>
                </div>
              </div>
            )}
            {nextLevelMeta && (
              <div className="selected-challenge-card muted">
                <div className="history-meta">
                  <span>Next route</span>
                  <span>{nextLevelMeta.unlocked ? 'Open soon' : 'Locked'}</span>
                </div>
                <strong>Lv {nextLevelMeta.level} · {nextLevelMeta.label}</strong>
                <p>{nextLevelMeta.description}</p>
              </div>
            )}
            <div className="retell-bounty-grid" style={{ marginTop: 18 }}>
              <div className="feedback-row mission-card">
                <div className="feedback-reason">Loop hook</div>
                <div className="feedback-after">Short runs, quick scores, rising unlock pressure.</div>
              </div>
              <div className="feedback-row mission-card">
                <div className="feedback-reason">Best habit</div>
                <div className="feedback-after">Replay the same route until the story structure feels automatic.</div>
              </div>
            </div>
          </div>
        </section>

        {progress?.attempts?.length ? (
          <section className="surface">
            <div className="section-head">
              <h2 className="section-title">Recent runs</h2>
              <span className="section-tag">{progress.total_attempts} total attempts</span>
            </div>
            <div className="recent-attempts">
              {progress.attempts.slice(0, 4).map((attempt) => (
                <div key={attempt.story_id + attempt.total_score} className="attempt-card">
                  <div className="attempt-score">{Math.round(attempt.total_score)}</div>
                  <div>
                    <div className="attempt-status">{attempt.passed ? 'Passed' : 'Retry needed'}</div>
                    <div className="attempt-copy">{attempt.next_tip}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    );
  }

  if (phase === 'listening' && story) {
    return (
      <div className="page-stack">
        <section className="immersive-stage retell-arena-stage live">
          <ImmersiveStageCanvas variant="retell" phase="listening" energy={arenaEnergy} secondary={story.level * 10 + 18} />
          <div className="immersive-stage-overlay retell-live-overlay">
            <div className="call-topline">
              <div>
                <div className="eyebrow">Story phase</div>
                <h1 className="page-title">{story.title}</h1>
                <p className="page-subtitle">
                  Level {story.level} · {story.story_mode === 'web_story' ? story.source_name : 'Tutor narration'} · Reward {boostedRewardPoints} pts
                </p>
              </div>
              <div className="room-actions">
                <div className="timer-chip">Lv {story.level}</div>
                <div className="timer-chip">{rankTitle}</div>
                <div className="timer-chip">{missionPressure}</div>
                <button className="secondary" onClick={handleReset}>Back to map</button>
              </div>
            </div>

            <div className="retell-mission-headline">
              <strong>Listen once, then trap the sequence in memory.</strong>
              <span>Catch the main idea first, then the turn, then the single detail that upgrades your score.</span>
            </div>

            <div className="retell-stage-audio-row">
              {story.audio_path ? (
                <audio
                  ref={storyAudioRef}
                  autoPlay
                  controls
                  src={story.audio_path}
                  onEnded={startCountdown}
                  className="story-audio-player"
                />
              ) : (
                <div className="error-banner">Server story audio is missing for this run. Please start the challenge again.</div>
              )}
              <button className="secondary" onClick={startCountdown}>
                Skip to countdown
              </button>
            </div>
          </div>
        </section>

        <section className="retell-layout">
          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Mission intel</h2>
              <span className="section-tag">{story.story_mode === 'web_story' ? 'British web audio' : 'British tutor voice'}</span>
            </div>

            {audioError && <div className="error-banner" style={{ marginTop: 14 }}>{audioError}</div>}

            <div className="retell-lock-grid" style={{ marginTop: 18 }}>
              <div className="lock-card">
                <span>Catch first</span>
                <strong>Main idea</strong>
                <p>What happened, to whom, and why it mattered.</p>
              </div>
              <div className="lock-card">
                <span>Hold next</span>
                <strong>Turning point</strong>
                <p>Notice where the story changes direction or gains tension.</p>
              </div>
              <div className="lock-card">
                <span>Upgrade with</span>
                <strong>One detail</strong>
                <p>Pick one object, place, or action you want to keep until your retell.</p>
              </div>
              <div className="lock-card">
                <span>Win style</span>
                <strong>Clear sequence</strong>
                <p>Use simple connectors more than fancy grammar.</p>
              </div>
            </div>

            <div className="retell-bounty-grid" style={{ marginTop: 18 }}>
              <div className="feedback-row">
                <div className="feedback-reason">What to catch</div>
                <div className="feedback-after">Main idea, sequence of events, one vivid detail.</div>
              </div>
              <div className="feedback-row">
                <div className="feedback-reason">What wins</div>
                <div className="feedback-after">A clear structure beats perfect grammar in this phase.</div>
              </div>
              <div className="feedback-row mission-card">
                <div className="feedback-reason">Live bonus</div>
                <div className="feedback-after">{bossStage ? 'Boss focus: no drifting.' : 'Hold the first key detail until the end.'}</div>
              </div>
              <div className="feedback-row mission-card">
                <div className="feedback-reason">Reward track</div>
                <div className="feedback-after">Current clear pays +{boostedRewardPoints} pts.</div>
              </div>
            </div>

            {story.source_url && (
              <p className="surface-copy" style={{ marginTop: 16 }}>
                Source: <a href={story.source_url} target="_blank" rel="noreferrer">{story.source_name}</a>
              </p>
            )}

            <button className="secondary" onClick={startCountdown} style={{ marginTop: 18 }}>
              I am ready to retell
            </button>
          </div>

          <div className="surface note-surface">
            <div className="section-head">
              <h2 className="section-title">Note window</h2>
              <span className="section-tag">Memory anchors</span>
            </div>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Hero, place, turning point, ending..."
              style={{ minHeight: 260 }}
            />
          </div>
        </section>
      </div>
    );
  }

  if (phase === 'countdown') {
    return (
      <div className="page-stack">
        <section className="immersive-stage retell-arena-stage countdown">
          <ImmersiveStageCanvas variant="retell" phase="countdown" energy={arenaEnergy} secondary={countdown * 8} />
          <div className="immersive-stage-overlay countdown-overlay">
            <div className="eyebrow">Gate opening</div>
            <div className="countdown-number">{countdown}</div>
            <p className="page-subtitle">Lead with the main idea, rebuild the timeline, and hold one vivid detail until the finish.</p>
          </div>
        </section>
      </div>
    );
  }

  if (phase === 'retelling') {
    const progressWidth = `${((RETELL_LIMIT_SECONDS - retellRemaining) / RETELL_LIMIT_SECONDS) * 100}%`;
    return (
      <div className="page-stack">
        <section className="immersive-stage retell-arena-stage live">
          <ImmersiveStageCanvas variant="retell" phase="retelling" energy={arenaEnergy} secondary={runProgressPercent} />
          <div className="immersive-stage-overlay retell-live-overlay">
            <div className="call-topline">
              <div>
                <div className="eyebrow">Retell phase</div>
                <h1 className="page-title">Tell the story back in your own words</h1>
                <p className="page-subtitle">You have up to {RETELL_LIMIT_SECONDS} seconds. Prioritise meaning before perfect grammar.</p>
              </div>
              <div className="room-actions">
                <div className="timer-chip">{retellRemaining}s left</div>
                <div className="timer-chip">+{boostedRewardPoints} pts live</div>
                <div className="timer-chip">{bossStage ? 'Boss route' : `${completedMissionCount}/3 missions`}</div>
                <button onClick={handleFinishRetelling} disabled={submitAudioMutation.isPending || submitMutation.isPending}>
                  {submitAudioMutation.isPending ? 'Transcribing...' : submitMutation.isPending ? 'Scoring...' : 'Finish run'}
                </button>
              </div>
            </div>

            <div className="retell-mission-headline">
              <strong>Hold the structure while the arena speed rises.</strong>
              <span>{liveTranscriptStatus}</span>
            </div>

            <div className="retell-live-hud">
              <div className="hud-pill">
                <span>Run timer</span>
                <strong>{RETELL_LIMIT_SECONDS - retellRemaining}s used</strong>
              </div>
              <div className="hud-pill">
                <span>Pressure</span>
                <strong>{missionPressure}</strong>
              </div>
              <div className="hud-pill">
                <span>Progress</span>
                <strong>{runProgressPercent}% through the run</strong>
              </div>
            </div>
          </div>
        </section>

        <section className="status-band">
          <div className="live-dot hot" />
          <div className="status-copy">
            <div className="status-title">Recording your retell</div>
            <div className="status-text">Speak continuously. Live transcript is used first; Qwen ASR runs only if live text is not usable.</div>
          </div>
          <div className="meter-panel">
            <div className="meter-label">
              <span>Run timer</span>
              <span>{RETELL_LIMIT_SECONDS - retellRemaining}s</span>
            </div>
            <div className="meter-track">
              <div className="meter-fill" style={{ width: progressWidth }} />
            </div>
            <div className="meter-caption">Auto-stop at {RETELL_LIMIT_SECONDS}s · pressure {missionPressure}</div>
          </div>
        </section>

        <section className="retell-lock-grid">
          {retellLocks.map((lock) => (
            <div key={lock.label} className="lock-card live">
              <span>{lock.label}</span>
              <strong>{Math.round(lock.value)}%</strong>
              <div className="meter-track" style={{ marginTop: 10 }}>
                <div className="meter-fill" style={{ width: `${lock.value}%` }} />
              </div>
              <p>{lock.copy}</p>
            </div>
          ))}
        </section>

        <section className="retell-layout">
          <div className="surface">
            {recordingError && <div className="error-banner">{recordingError}</div>}
            <textarea
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder="Live transcript appears here while you retell..."
              style={{ minHeight: 240, marginBottom: 16 }}
            />
            <div className="room-actions" style={{ justifyContent: 'flex-start' }}>
              <button
                className="secondary"
                onClick={handleSubmitTyped}
                disabled={submitMutation.isPending || submitAudioMutation.isPending || !transcript.trim()}
              >
                {submitMutation.isPending ? 'Scoring...' : 'Submit typed backup'}
              </button>
            </div>
          </div>

          <div className="surface note-surface">
            <div className="section-head">
              <h2 className="section-title">Quick notes</h2>
              <span className="section-tag">Visible during retell</span>
            </div>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Keep the structure visible, not full sentences..."
              style={{ minHeight: 260 }}
            />
          </div>
        </section>
      </div>
    );
  }

  if (phase === 'scored' && score) {
    return (
      <div className="page-stack">
        <section className="immersive-stage retell-arena-stage score">
          <ImmersiveStageCanvas
            variant="retell"
            phase={score.passed ? 'scored_win' : 'scored_retry'}
            energy={score.passed ? 92 : 56}
            secondary={Math.round(score.total_score)}
          />
          <div className="immersive-stage-overlay retell-score-overlay">
            <div className="stage-kicker-group">
              <div className="eyebrow">Result</div>
              <div className="status-strip">
                <div className="status-pill quiet">{score.passed ? `+${boostedRewardPoints} pts secured` : 'Retry to cash the reward'}</div>
                <div className="status-pill quiet">{score.passed ? 'Unlock pressure rising' : 'One more clean run'}</div>
                <div className="status-pill quiet">{score.passed ? `Streak ${Math.max(1, progress?.win_streak || 1)} alive` : 'Streak reset risk'}</div>
              </div>
            </div>
            <div className="retell-stage-copy">
              <h1 className="page-title">{score.passed ? 'Stage cleared' : 'Almost there'}</h1>
              <p className="page-subtitle">{score.next_tip}</p>
            </div>
            <div className="score-hero">
              <div className={`score-orb ${score.passed ? 'success' : 'warning'}`}>{Math.round(score.total_score)}</div>
            </div>
          </div>
        </section>

        <section className="metrics-strip">
          {Object.entries(score.scores || {}).map(([key, value]) => (
            <div key={key} className="metric-box">
              <span>{key.replace(/_/g, ' ')}</span>
              <strong>{Math.round(value)}</strong>
            </div>
          ))}
          <div className="metric-box">
            <span>Reward</span>
            <strong>{score.passed ? `+${boostedRewardPoints}` : '+0'}</strong>
          </div>
        </section>

        <section className="score-reward-grid">
          <div className="surface reward-card">
            <div className="history-meta">
              <span>Reward vault</span>
              <span>{score.passed ? 'Secured' : 'Missed'}</span>
            </div>
            <strong>{score.passed ? `+${boostedRewardPoints} pts` : 'No clear bonus yet'}</strong>
            <p>{score.passed ? 'The route is cleared and your unlock pressure rises.' : 'One cleaner run cashes the multiplier and keeps the streak alive.'}</p>
          </div>
          <div className="surface reward-card">
            <div className="history-meta">
              <span>Next route</span>
              <span>{nextLevelMeta?.unlocked ? 'Ready' : 'Target'}</span>
            </div>
            <strong>{nextLevelMeta ? `Lv ${nextLevelMeta.level} · ${nextLevelMeta.label}` : 'Keep climbing'}</strong>
            <p>{nextLevelMeta?.description || 'Keep clearing stages to open the next layer of difficulty.'}</p>
          </div>
          <div className="surface reward-card">
            <div className="history-meta">
              <span>Coach verdict</span>
              <span>{score.passed ? 'Advance' : 'Retry'}</span>
            </div>
            <strong>{score.next_tip}</strong>
            <p>Use the next run to stabilise structure first, then sharpen wording.</p>
          </div>
        </section>

        <section className="retell-layout">
          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">What you captured</h2>
              <span className="section-tag">{score.passed ? 'Pass' : 'Retry'}</span>
            </div>
            {score.transcript ? (
              <div className="transcript-box">{score.transcript}</div>
            ) : null}

            <div className="feedback-columns">
              <div>
                <h3 className="mini-heading">Key points covered</h3>
                <div className="stack-list">
                  {score.key_points_covered?.length ? score.key_points_covered.map((point, index) => (
                    <div key={index} className="positive-row">+ {point}</div>
                  )) : <div className="empty-copy">No covered points were extracted this time.</div>}
                </div>
              </div>
              <div>
                <h3 className="mini-heading">Key points missed</h3>
                <div className="stack-list">
                  {score.key_points_missed?.length ? score.key_points_missed.map((point, index) => (
                    <div key={index} className="warning-row">- {point}</div>
                  )) : <div className="empty-copy">Nice. No major key points were flagged as missing.</div>}
                </div>
              </div>
            </div>

            <div className="teacher-box">
              <div className="mini-heading">Teacher feedback</div>
              <p>{score.advice}</p>
            </div>

            {score.advice_audio_path && (
              <button className="secondary" onClick={() => playFeedbackAudio(score.advice_audio_path)}>
                Play teacher audio
              </button>
            )}
          </div>

          <div className="surface note-surface">
            <div className="section-head">
              <h2 className="section-title">Replay notes</h2>
              <span className="section-tag">Improve next run</span>
            </div>

            <div className="stack-list">
              {score.missed_points?.map((point, index) => (
                <div key={index} className="warning-row">{point}</div>
              ))}
              {score.language_feedback?.map((item, index) => (
                <div key={index} className="feedback-row">
                  <div className="feedback-after">{item}</div>
                </div>
              ))}
              {!score.missed_points?.length && !score.language_feedback?.length && (
                <div className="empty-copy">No extra misses or language warnings were added for this run.</div>
              )}
            </div>

            <button onClick={handleReset} style={{ marginTop: 18 }}>
              {score.passed ? 'Back to level map' : 'Try another run'}
            </button>
          </div>
        </section>
      </div>
    );
  }

  return null;
}
