import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import InterrogationScene from '../components/InterrogationScene';
import { openPreferredAudioStream } from '../utils/audioInput';

interface NotebookEntry {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  replyId?: string;
}

interface IntelCard {
  label: string;
  value: string;
  detail: string;
}

interface PhaseCard {
  key: string;
  label: string;
  objective: string;
  threshold: number;
  reward_hint: string;
}

interface LoadoutCard {
  label: string;
  detail: string;
}

interface DirectiveCard {
  key: string;
  label: string;
  detail: string;
  reward: number;
  progress: number;
  target: number;
  completed: boolean;
}

interface InterrogationStartPayload {
  run_id: number;
  case_code: string;
  operation_name: string;
  topic: string;
  suspect_name: string;
  suspect_title: string;
  mode_key: string;
  mode_label: string;
  mode_blurb: string;
  threat_level: string;
  mission_brief: string;
  opening_scene: string;
  defense_meter: number;
  target_rounds: number;
  active_phase: number;
  difficulty_label: string;
  difficulty_stars: number;
  dossier: IntelCard[];
  phases: PhaseCard[];
  loadout: LoadoutCard[];
  directives: DirectiveCard[];
}

interface ReplyLatency {
  first_text_ms: number;
  first_audio_ms: number;
}

interface FlashNotice {
  title: string;
  detail: string;
}

function wsUrlFor(path: string) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${path}`;
}

const modeDoctrine: Record<string, { headline: string; detail: string; failure: string }> = {
  classic: {
    headline: 'Balanced extraction curve',
    detail: 'You have room to build layered answers, trigger phase breaks, and cash out directives over time.',
    failure: 'The contract goes cold if you stay vague for too many turns in a row.',
  },
  onslaught: {
    headline: 'Explosive high-risk push',
    detail: 'Early combos matter more than anything. If you seize tempo first, the payout spikes fast.',
    failure: 'Heat climbs brutally here. One weak answer can flip the whole room against you.',
  },
  precision: {
    headline: 'Technical short-window dossier',
    detail: 'Fewer turns, harsher scoring. The chamber wants exact structure, grounded examples, and no hedge language.',
    failure: 'Abstract talk wastes the window. If each answer lacks shape, the contract expires before pressure lands.',
  },
};

export default function InterrogationGamePage() {
  const [searchParams] = useSearchParams();
  const [run, setRun] = useState<InterrogationStartPayload | null>(null);
  const [notebook, setNotebook] = useState<NotebookEntry[]>([]);
  const [partialTranscript, setPartialTranscript] = useState('');
  const [defenseMeter, setDefenseMeter] = useState(100);
  const [recording, setRecording] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [micEnergy, setMicEnergy] = useState(0);
  const [statusMessage, setStatusMessage] = useState('Booting the chamber...');
  const [summary, setSummary] = useState<any>(null);
  const [analysisLabel, setAnalysisLabel] = useState('steady');
  const [latestAnalysis, setLatestAnalysis] = useState<any>(null);
  const [pressureDelta, setPressureDelta] = useState(0);
  const [shockPulse, setShockPulse] = useState(false);
  const [roomEntered, setRoomEntered] = useState(false);
  const [activePhase, setActivePhase] = useState(0);
  const [combo, setCombo] = useState(0);
  const [heat, setHeat] = useState(10);
  const [phaseBreaks, setPhaseBreaks] = useState(0);
  const [hitTags, setHitTags] = useState<string[]>([]);
  const [weaknessTags, setWeaknessTags] = useState<string[]>([]);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [latency, setLatency] = useState<ReplyLatency | null>(null);
  const [operationSeconds, setOperationSeconds] = useState(0);
  const [directives, setDirectives] = useState<DirectiveCard[]>([]);
  const [bonusReward, setBonusReward] = useState(0);
  const [flashNotice, setFlashNotice] = useState<FlashNotice | null>(null);
  const [entrySequence, setEntrySequence] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const pcmBufferRef = useRef<Float32Array[]>([]);
  const pcmSampleCountRef = useRef(0);
  const audioQueueRef = useRef<Array<{ audio: string; replyId?: string }>>([]);
  const playingAudioRef = useRef<HTMLAudioElement | null>(null);
  const mountedRef = useRef(true);
  const recordingRef = useRef(false);
  const activeReplyIdRef = useRef<string | null>(null);
  const mutedReplyIdsRef = useRef<Set<string>>(new Set());
  const roomEnteredAtRef = useRef<number | null>(null);
  const requestedMode = (searchParams.get('mode') || 'classic').trim().toLowerCase();

  useEffect(() => {
    mountedRef.current = true;
    void bootRoom(requestedMode);
    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
      if (playingAudioRef.current) {
        playingAudioRef.current.pause();
        playingAudioRef.current = null;
      }
      sourceNodeRef.current?.disconnect();
      workletNodeRef.current?.disconnect();
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      void audioContextRef.current?.close();
    };
  }, [requestedMode]);

  const lieDetectorPath = useMemo(() => {
    const pulse = 14 + micEnergy * 18 + Math.max(0, pressureDelta) * 0.5 + heat * 0.16;
    const points = Array.from({ length: 34 }, (_, index) => {
      const x = (index / 33) * 100;
      const variance = Math.sin(index * 0.84 + micEnergy * 4.2) * pulse * (index % 6 === 0 ? 1.18 : 0.58);
      const y = 50 + variance;
      return `${x},${Math.max(8, Math.min(92, y))}`;
    });
    return points.join(' ');
  }, [heat, micEnergy, pressureDelta]);

  const bootRoom = async (modeOverride?: string) => {
    const payload = await api.startInterrogation({ mode: modeOverride || requestedMode || run?.mode_key || 'classic' });
    if (!mountedRef.current) return;
    wsRef.current?.close();
    mutedReplyIdsRef.current.clear();
    activeReplyIdRef.current = null;
    audioQueueRef.current = [];
    playingAudioRef.current?.pause();
    playingAudioRef.current = null;
    setNotebook([]);
    setSummary(null);
    setCollapsed(false);
    setLatestAnalysis(null);
    setPressureDelta(0);
    setCombo(0);
    setHeat(10);
    setPhaseBreaks(0);
    setHitTags([]);
    setWeaknessTags([]);
    setAudioPlaying(false);
    setLatency(null);
    setDirectives(payload.directives || []);
    setBonusReward(0);
    setFlashNotice(null);
    setEntrySequence(false);
    setOperationSeconds(0);
    setRoomEntered(false);
    roomEnteredAtRef.current = null;
    setRun(payload);
    setDefenseMeter(payload.defense_meter);
    setActivePhase(payload.active_phase);
    setStatusMessage(payload.opening_scene);
  };

  const interruptCurrentReply = () => {
    const activeReplyId = activeReplyIdRef.current;
    if (activeReplyId) mutedReplyIdsRef.current.add(activeReplyId);
    if (playingAudioRef.current) {
      playingAudioRef.current.pause();
      playingAudioRef.current = null;
    }
    audioQueueRef.current = [];
    setAudioPlaying(false);
    setThinking(false);
  };

  const connectSocket = (runId: number) => {
    const socket = new WebSocket(wsUrlFor(`/api/games/interrogation/${runId}/stream`));
    wsRef.current = socket;
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === 'reply.text.delta') {
        const replyId = message.reply_id || `reply-${message.chunk_index || Date.now()}`;
        if (mutedReplyIdsRef.current.has(replyId)) return;
        activeReplyIdRef.current = replyId;
        setThinking(false);
        setNotebook((previous) => {
          const next = [...previous];
          const last = next[next.length - 1];
          if (last?.role === 'assistant' && last.replyId === replyId) {
            if (last.text === message.text || last.text.endsWith(message.text)) return next;
            if (message.text.startsWith(last.text)) {
              last.text = message.text;
              return next;
            }
            const merged = `${last.text} ${message.text}`.replace(/\s+/g, ' ').trim();
            if (merged === last.text) return next;
            last.text = merged;
            return next;
          }
          next.push({
            id: `assistant-${Date.now()}-${next.length}`,
            role: 'assistant',
            text: message.text,
            replyId,
          });
          return next;
        });
        setStatusMessage('The suspect shifts and answers back.');
        return;
      }
      if (message.type === 'reply.audio.chunk') {
        if (message.reply_id && mutedReplyIdsRef.current.has(message.reply_id)) return;
        enqueueAudio(message.audio, message.reply_id);
        return;
      }
      if (message.type === 'ptt.partial') {
        setPartialTranscript(message.text || '');
        return;
      }
      if (message.type === 'transcript.commit') {
        setPartialTranscript('');
        setNotebook((previous) => [
          ...previous,
          { id: `user-${Date.now()}-${previous.length}`, role: 'user', text: message.text },
        ]);
        return;
      }
      if (message.type === 'suspect.thinking') {
        activeReplyIdRef.current = message.reply_id || activeReplyIdRef.current;
        setThinking(true);
        setStatusMessage(message.message || 'Tape rewinding...');
        return;
      }
      if (message.type === 'meter.update') {
        setDefenseMeter(message.defense_meter);
        setAnalysisLabel(message.label || 'steady');
        setLatestAnalysis(message.analysis || null);
        setPressureDelta(message.delta || 0);
        setCombo(message.combo || 0);
        setHeat(message.heat || 0);
        setHitTags(message.hit_tags || []);
        setWeaknessTags(message.weakness_tags || []);
        setDirectives(message.directives || []);
        setBonusReward(message.bonus_reward || 0);
        setShockPulse(true);
        window.setTimeout(() => setShockPulse(false), 320);
        setStatusMessage(
          message.label === 'dominant'
            ? 'That strike landed. Keep the pressure and do not soften now.'
            : message.label === 'shaky'
              ? 'The room did not buy that answer. Rebuild with structure and support.'
              : 'The suspect moved, but not enough. Tighten the next turn.',
        );
        return;
      }
      if (message.type === 'directive.update') {
        setDirectives(message.directives || []);
        setBonusReward(message.bonus_reward || 0);
        const firstUnlocked = message.unlocked?.[0];
        if (firstUnlocked) {
          setFlashNotice({
            title: `${firstUnlocked.label} secured`,
            detail: `+${firstUnlocked.reward} credits banked for the collapse payout`,
          });
        }
        return;
      }
      if (message.type === 'phase.update') {
        setActivePhase(message.active_phase || 0);
        setCombo(message.combo || 0);
        setHeat(message.heat || 0);
        setPhaseBreaks(message.phase_breaks || 0);
        if (message.phase?.label) {
          setStatusMessage(`${message.phase.label}: ${message.phase.objective}`);
          if (roomEntered) {
            setFlashNotice({
              title: message.phase.label,
              detail: message.phase.objective,
            });
          }
        }
        return;
      }
      if (message.type === 'collapse') {
        setCollapsed(true);
        setStatusMessage(message.message || 'The suspect folds.');
        return;
      }
      if (message.type === 'round.end') {
        recordingRef.current = false;
        setRecording(false);
        setThinking(false);
        if (typeof message.combo === 'number') setCombo(message.combo);
        if (typeof message.heat === 'number') setHeat(message.heat);
        if (message.metrics) setLatency(message.metrics);
        if (message.message) setStatusMessage(message.message);
        if (message.interrupted) {
          setStatusMessage(message.message || 'You cut the tape and took control of the room again.');
        }
        if (message.summary) setSummary(message.summary);
      }
    };
  };

  const enterRoom = () => {
    if (!run || roomEntered) return;
    setRoomEntered(true);
    setEntrySequence(true);
    roomEnteredAtRef.current = Date.now();
    window.setTimeout(() => connectSocket(run.run_id), 650);
    window.setTimeout(() => setEntrySequence(false), 2200);
  };

  useEffect(() => {
    if (!flashNotice) return undefined;
    const timer = window.setTimeout(() => setFlashNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [flashNotice]);

  useEffect(() => {
    if (!roomEntered || summary) return undefined;
    const timer = window.setInterval(() => {
      if (!roomEnteredAtRef.current) return;
      setOperationSeconds(Math.floor((Date.now() - roomEnteredAtRef.current) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [roomEntered, summary]);

  const enqueueAudio = (base64Audio: string, replyId?: string) => {
    audioQueueRef.current.push({ audio: base64Audio, replyId });
    if (!playingAudioRef.current) playNextAudio();
  };

  const playNextAudio = () => {
    const next = audioQueueRef.current.shift();
    if (!next) {
      playingAudioRef.current = null;
      setAudioPlaying(false);
      return;
    }
    if (next.replyId && mutedReplyIdsRef.current.has(next.replyId)) {
      playNextAudio();
      return;
    }
    const audio = new Audio(`data:audio/wav;base64,${next.audio}`);
    playingAudioRef.current = audio;
    setAudioPlaying(true);
    audio.onended = () => {
      playingAudioRef.current = null;
      playNextAudio();
    };
    audio.play().catch(() => {
      playingAudioRef.current = null;
      setAudioPlaying(false);
      playNextAudio();
    });
  };

  const downsampleTo16k = (input: Float32Array, sampleRate: number) => {
    if (sampleRate === 16000) return new Float32Array(input);
    const ratio = sampleRate / 16000;
    const outputLength = Math.floor(input.length / ratio);
    const output = new Float32Array(outputLength);
    for (let i = 0; i < outputLength; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(input.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = start; j < end; j += 1) sum += input[j];
      output[i] = sum / Math.max(1, end - start);
    }
    return output;
  };

  const encodePcm16 = (samples: Float32Array) => {
    const pcm = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    const bytes = new Uint8Array(pcm.buffer);
    let binary = '';
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  };

  const flushPcm = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    if (!pcmBufferRef.current.length) return;
    const joinedLength = pcmBufferRef.current.reduce((sum, item) => sum + item.length, 0);
    const joined = new Float32Array(joinedLength);
    let cursor = 0;
    pcmBufferRef.current.forEach((item) => {
      joined.set(item, cursor);
      cursor += item.length;
    });
    wsRef.current.send(JSON.stringify({ type: 'audio.chunk', data: encodePcm16(joined) }));
    pcmBufferRef.current = [];
    pcmSampleCountRef.current = 0;
  };

  const ensureCaptureGraph = async () => {
    if (!mediaStreamRef.current) {
      mediaStreamRef.current = await openPreferredAudioStream();
    }
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext();
      await audioContextRef.current.audioWorklet.addModule('/audio-worklets/pcm-capture.js');
    }
    if (audioContextRef.current.state === 'suspended') {
      await audioContextRef.current.resume();
    }
    if (!sourceNodeRef.current || !workletNodeRef.current) {
      const source = audioContextRef.current.createMediaStreamSource(mediaStreamRef.current);
      const node = new AudioWorkletNode(audioContextRef.current, 'pcm-capture-processor');
      node.port.onmessage = (event) => {
        if (!recordingRef.current) return;
        const frame = event.data as Float32Array;
        const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / Math.max(1, frame.length));
        setMicEnergy(Math.min(1, rms * 8));
        const downsampled = downsampleTo16k(frame, audioContextRef.current?.sampleRate || 48000);
        if (!downsampled.length) return;
        pcmBufferRef.current.push(downsampled);
        pcmSampleCountRef.current += downsampled.length;
        if (pcmSampleCountRef.current >= 3200) flushPcm();
      };
      source.connect(node);
      node.connect(audioContextRef.current.destination);
      sourceNodeRef.current = source;
      workletNodeRef.current = node;
    }
  };

  const startRecording = async () => {
    if (!run || recordingRef.current || collapsed || !roomEntered || entrySequence || summary) return;
    if (thinking || audioPlaying) {
      skipReply();
      setStatusMessage('You cut the suspect off. Press again and take the floor.');
      return;
    }
    interruptCurrentReply();
    await ensureCaptureGraph();
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    setStatusMessage('Tape rolling. Drive the suspect backward with one clean turn.');
    recordingRef.current = true;
    setRecording(true);
    setPartialTranscript('');
    pcmBufferRef.current = [];
    pcmSampleCountRef.current = 0;
    wsRef.current.send(JSON.stringify({ type: 'ptt.start' }));
  };

  const stopRecording = () => {
    if (!recordingRef.current || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    recordingRef.current = false;
    flushPcm();
    wsRef.current.send(JSON.stringify({ type: 'ptt.stop' }));
    setRecording(false);
    setThinking(true);
    setMicEnergy(0);
    setStatusMessage('Tape rewinding...');
  };

  const skipReply = () => {
    interruptCurrentReply();
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'skip_reply' }));
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || event.repeat) return;
      event.preventDefault();
      if (!recordingRef.current) void startRecording();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== 'Space') return;
      event.preventDefault();
      if (recordingRef.current) stopRecording();
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  });

  const roundCount = useMemo(
    () => notebook.filter((entry) => entry.role === 'user').length,
    [notebook],
  );

  const latestSuspectLine = useMemo(
    () => [...notebook].reverse().find((entry) => entry.role === 'assistant')?.text || 'No verbal tell yet.',
    [notebook],
  );

  const currentPhase = useMemo(
    () => run?.phases?.[activePhase] || null,
    [activePhase, run?.phases],
  );

  const directivesCompleted = useMemo(
    () => directives.filter((item) => item.completed).length,
    [directives],
  );

  const missionScore = useMemo(() => {
    const base = (100 - defenseMeter) * 7 + combo * 55 + phaseBreaks * 130 + roundCount * 18 - heat * 2;
    return Math.max(0, Math.round(base));
  }, [combo, defenseMeter, heat, phaseBreaks, roundCount]);

  const operationRank = useMemo(() => {
    if (collapsed) return 'S';
    if (missionScore >= 720) return 'A';
    if (missionScore >= 520) return 'B';
    if (missionScore >= 320) return 'C';
    return 'D';
  }, [collapsed, missionScore]);

  const objectiveChecks = useMemo(() => {
    const analysis = latestAnalysis || {};
    return [
      {
        label: 'State a clear position',
        done: (analysis.word_count ?? 0) >= 12,
      },
      {
        label: 'Use a sharp connector',
        done: (analysis.advanced_linkers ?? 0) > 0,
      },
      {
        label: 'Back it with an example',
        done: (analysis.example_count ?? 0) > 0,
      },
    ];
  }, [latestAnalysis]);

  const heatSeverity = heat >= 70 ? 'critical' : heat >= 40 ? 'elevated' : 'controlled';
  const formattedClock = `${String(Math.floor(operationSeconds / 60)).padStart(2, '0')}:${String(operationSeconds % 60).padStart(2, '0')}`;
  const difficultyStars = '★'.repeat(run?.difficulty_stars || 0);
  const pressureCopy = useMemo(() => {
    if (collapsed) return 'The room finally gave you the confession.';
    if (!roomEntered) return 'Read the dossier, map the phases, then enter when you are ready.';
    if (entrySequence) return 'Steel door sealed. Recorder armed. Eyes on the suspect.';
    if (recording) return 'Good. Stay fluent and commit to a complete thought.';
    if (thinking) return 'The tape is rewinding your last strike through the room.';
    if (pressureDelta >= 18) return 'You have the suspect under real pressure now.';
    if (pressureDelta < 0) return 'That turn fed the room. Reset and come back with structure.';
    return 'The suspect is still alive in the argument. Build the next phase break.';
  }, [collapsed, entrySequence, pressureDelta, recording, roomEntered, thinking]);

  const phaseTimeline = run?.phases || [];
  const loadoutCount = run?.loadout?.length ?? 0;
  const topLoadout = loadoutCount ? run!.loadout : [{ label: 'No active chips', detail: 'Buy upgrades in the command center to alter future operations.' }];
  const chamberMood = collapsed
    ? 'Confession secured'
    : thinking || audioPlaying
      ? 'Suspect is pushing back'
      : recording
        ? 'Your move is live'
        : 'The chamber is waiting';
  const summaryOutcome = summary?.collapsed ? 'Confession secured' : 'Target extracted';
  const roundsRemaining = Math.max(0, (run?.target_rounds || 0) - roundCount);
  const contractProgress = run?.target_rounds ? Math.min(100, (roundCount / run.target_rounds) * 100) : 0;
  const doctrine = modeDoctrine[run?.mode_key || requestedMode] || modeDoctrine.classic;
  const contractAlert = useMemo(() => {
    if (summary) {
      return {
        tone: summary.collapsed ? 'success' : 'warning',
        title: summary.collapsed ? 'Confession secured' : 'Window closed',
        detail: summary.collapsed
          ? 'Extraction succeeded before the chamber could cool.'
          : 'This contract ended before the suspect fully broke.',
      };
    }
    if (!roomEntered) {
      return {
        tone: 'steady',
        title: 'Briefing stage',
        detail: 'Map the directives and room risks before you lock the door.',
      };
    }
    if (heat >= 85) {
      return {
        tone: 'danger',
        title: 'Overheat danger',
        detail: 'Another weak turn may burn the contract before the suspect collapses.',
      };
    }
    if (roundsRemaining <= 1) {
      return {
        tone: 'warning',
        title: 'Last contract window',
        detail: 'You are almost out of live turns. The next answer must land cleanly.',
      };
    }
    if (combo >= 3) {
      return {
        tone: 'success',
        title: 'Combo pressure live',
        detail: 'Momentum is finally stacked in your favour. This is the time to finish with an example.',
      };
    }
    return {
      tone: 'steady',
      title: 'Pressure building',
      detail: 'The room is still evaluating you. Keep structure high and avoid cheap filler.',
    };
  }, [combo, heat, roomEntered, roundsRemaining, summary]);
  const operatorWhisper = useMemo(() => {
    if (!roomEntered) {
      return {
        title: 'Choose your line before the tape rolls.',
        detail: 'The first clean turn decides whether the suspect respects you or starts dictating the pace.',
      };
    }
    if (recording) {
      return {
        title: 'Commit to one full idea.',
        detail: 'Open with a position, connect the reason, and lock it in with one concrete example.',
      };
    }
    if (thinking || audioPlaying) {
      return {
        title: 'Listen for the counter-punch.',
        detail: 'The suspect’s next line reveals which part of your answer actually landed.',
      };
    }
    if (heat >= 70) {
      return {
        title: 'Cool the chamber without softening.',
        detail: 'Use one clear connector and one grounded detail. Do not fill the silence with hedge language.',
      };
    }
    if (combo >= 2) {
      return {
        title: 'Cash the combo now.',
        detail: 'You have tempo. Use it on a stronger example or a tighter cause-and-effect chain.',
      };
    }
    return {
      title: 'The room still wants sharper proof.',
      detail: 'Longer answers help, but only if they move from claim to support without drifting.',
    };
  }, [audioPlaying, combo, heat, recording, roomEntered, thinking]);
  const transcriptExcerpt = useMemo(() => {
    if (!summary?.transcript) return 'No extraction tape was archived for this contract.';
    const lines = String(summary.transcript).split('\n').filter(Boolean);
    return (lines.slice(-2).join(' ') || lines[0] || '').trim();
  }, [summary?.transcript]);

  return (
    <div
      className={`interrogation-room interrogation-room-cinematic mode-${run?.mode_key || requestedMode} ${collapsed ? 'collapsed' : ''} ${recording ? 'recording' : ''} ${thinking ? 'thinking' : ''} ${shockPulse ? 'shock' : ''}`}
    >
      <InterrogationScene
        recording={recording}
        thinking={thinking}
        defenseMeter={defenseMeter}
        collapsed={collapsed}
        pressureDelta={pressureDelta}
      />

      <div className="interrogation-overlay interrogation-overhaul">
        {flashNotice ? (
          <motion.div
            className="interrogation-floating-notice"
            initial={{ opacity: 0, y: -22, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0 }}
          >
            <span>Directive Complete</span>
            <strong>{flashNotice.title}</strong>
            <p>{flashNotice.detail}</p>
          </motion.div>
        ) : null}

        {roomEntered && entrySequence ? (
          <motion.div
            className="interrogation-entry-sequence"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <div className="entry-sequence-card">
              <span>Black Chamber Sync</span>
              <strong>{run?.operation_name}</strong>
              <p>Door sealed. Lamp stabilising. Recorder deck syncing to the suspect line.</p>
              <div className="entry-sequence-steps">
                <div><span /> Chamber locked</div>
                <div><span /> Recorder armed</div>
                <div><span /> Wiretap live</div>
              </div>
            </div>
          </motion.div>
        ) : null}

        <header className="interrogation-commandbar overhaul">
          <div className="interrogation-command-copy">
            <span className="dispatch-tag">BLACK CHAMBER</span>
            <h1>{run?.operation_name || 'Preparing operation...'}</h1>
            <p>{run ? `${run.case_code} · ${run.suspect_name}, ${run.suspect_title} · ${run.topic}` : 'Loading suspect dossier...'}</p>
          </div>
          <div className="interrogation-command-meta">
            <div className="interrogation-command-seal">
              <span>{run?.mode_label || 'Contract syncing'}</span>
              <strong>{run?.difficulty_label || 'Calibrating Room'}</strong>
              <p>{run?.mode_blurb || chamberMood}</p>
            </div>
            <div className="dispatch-actions">
              <Link to="/games" className="secondary game-back-link">Back to Command</Link>
              <button
                type="button"
                className="secondary"
                onClick={() => void bootRoom(run?.mode_key || requestedMode)}
              >
                New Briefing
              </button>
            </div>
          </div>
        </header>

        <section className="interrogation-status-ribbon">
          <div className="interrogation-status-chip">
            <span>Operation Clock</span>
            <strong>{formattedClock}</strong>
          </div>
          <div className="interrogation-status-chip">
            <span>Turns Left</span>
            <strong>{run?.target_rounds ? roundsRemaining : '--'}</strong>
          </div>
          <div className="interrogation-status-chip">
            <span>Mission Score</span>
            <strong>{missionScore}</strong>
          </div>
          <div className="interrogation-status-chip">
            <span>Rank</span>
            <strong>{operationRank}</strong>
          </div>
          <div className="interrogation-status-chip">
            <span>Directive bank</span>
            <strong>+{bonusReward}</strong>
          </div>
          <div className={`interrogation-status-chip ${contractAlert.tone}`}>
            <span>Alert State</span>
            <strong>{contractAlert.title}</strong>
          </div>
          <div className={`interrogation-status-chip ${thinking || audioPlaying ? 'live' : ''}`}>
            <span>Turn State</span>
            <strong>{recording ? 'Talking' : thinking ? 'Thinking' : audioPlaying ? 'Speaking' : 'Ready'}</strong>
          </div>
        </section>

        <section className="interrogation-hud-strip overhaul">
          <article className="interrogation-hud-card focus">
            <span>Defense meter</span>
            <strong>{defenseMeter}%</strong>
            <p>{defenseMeter > 65 ? 'Still guarded' : defenseMeter > 35 ? 'Story weakening' : 'Confession window opening'}</p>
          </article>
          <article className={`interrogation-hud-card ${combo >= 2 ? 'good' : ''}`}>
            <span>Combo</span>
            <strong>x{combo}</strong>
            <p>Strong consecutive turns make later answers hit much harder.</p>
          </article>
          <article className={`interrogation-hud-card ${heat >= 60 ? 'bad' : ''}`}>
            <span>Heat</span>
            <strong>{heat}</strong>
            <p>{heatSeverity === 'critical' ? 'You are feeding the room.' : heatSeverity === 'elevated' ? 'Pressure is unstable.' : 'You still own the tempo.'}</p>
          </article>
          <article className="interrogation-hud-card">
            <span>Phase breaks</span>
            <strong>{phaseBreaks}</strong>
            <p>Every phase break unlocks a bigger end-of-run payout.</p>
          </article>
        </section>

        <section className="interrogation-phase-track">
          {phaseTimeline.map((phase, index) => (
            <div
              key={phase.key}
              className={`interrogation-phase-card ${index === activePhase ? 'active' : ''} ${index < activePhase ? 'cleared' : ''}`}
            >
              <span>{phase.label}</span>
              <strong>{phase.objective}</strong>
              <p>{phase.reward_hint}</p>
            </div>
          ))}
        </section>

        <section className="interrogation-battle-grid">
          <aside className="interrogation-intel-column">
            <div className="interrogation-objective-panel">
              <span className="dispatch-tag">Live objective</span>
              <h2>{currentPhase?.label || 'Awaiting phase'}</h2>
              <p>{currentPhase?.objective || pressureCopy}</p>
            </div>

            <div className="interrogation-dossier-grid">
              {run?.dossier?.map((card) => (
                <div key={card.label} className="interrogation-dossier-card">
                  <span>{card.label}</span>
                  <strong>{card.value}</strong>
                  <p>{card.detail}</p>
                </div>
              ))}
            </div>

            <div className="interrogation-loadout-panel">
              <div className="diegetic-topline">
                <span>Loadout</span>
                <strong>{loadoutCount}</strong>
              </div>
              <div className="interrogation-loadout-list">
                {topLoadout.map((item) => (
                  <div key={item.label} className="interrogation-loadout-card">
                    <strong>{item.label}</strong>
                    <p>{item.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="interrogation-tags-panel">
              <div className="diegetic-topline">
                <span>Last read</span>
                <strong>{analysisLabel}</strong>
              </div>
              <div className="interrogation-chip-row">
                {(hitTags.length ? hitTags : ['no clean hit yet']).map((tag) => (
                  <span key={`hit-${tag}`} className="interrogation-chip good">{tag}</span>
                ))}
              </div>
              <div className="interrogation-chip-row">
                {(weaknessTags.length ? weaknessTags : ['no penalty']).map((tag) => (
                  <span key={`weak-${tag}`} className="interrogation-chip bad">{tag}</span>
                ))}
              </div>
            </div>

            <div className="interrogation-tactical-panel">
              <div className="diegetic-topline">
                <span>Live turn checklist</span>
                <strong>{objectiveChecks.filter((item) => item.done).length}/3</strong>
              </div>
              <div className="interrogation-objective-list">
                {objectiveChecks.map((item) => (
                  <div key={item.label} className={`objective-check ${item.done ? 'done' : ''}`}>
                    <span />
                    <p>{item.label}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="interrogation-directives-panel">
              <div className="diegetic-topline">
                <span>Directive vault</span>
                <strong>{directivesCompleted}/{directives.length || 3}</strong>
              </div>
              <div className="interrogation-directive-list">
                {directives.map((directive) => (
                  <div key={directive.key} className={`directive-card ${directive.completed ? 'completed' : ''}`}>
                    <div className="directive-card-head">
                      <strong>{directive.label}</strong>
                      <span>+{directive.reward}</span>
                    </div>
                    <p>{directive.detail}</p>
                    <div className="directive-progress">
                      <div style={{ width: `${(directive.progress / Math.max(1, directive.target)) * 100}%` }} />
                    </div>
                    <small>{directive.completed ? 'Secured' : `${directive.progress}/${directive.target} progress`}</small>
                  </div>
                ))}
              </div>
            </div>
          </aside>

          <section className="interrogation-scene-column">
            <div className="interrogation-stage-shell cinematic">
              <div className="interrogation-stage-copy">
                <span className="dispatch-tag">Room pressure</span>
                <h2>{pressureCopy}</h2>
                <p>{statusMessage}</p>
              </div>
              <div className="interrogation-stage-indicators">
                <div className="interrogation-defense-segments">
                  {Array.from({ length: 5 }, (_, index) => (
                    <span key={`segment-${index}`} className={index < Math.ceil(defenseMeter / 20) ? 'active' : ''} />
                  ))}
                </div>
                <div className="interrogation-keybind-hint">Hold Space or press the deck to speak</div>
              </div>
            </div>

            <div className="interrogation-ambient-kicker">
              <div>
                <span>Contract window</span>
                <strong>{run?.mode_label || 'Pending contract'}</strong>
              </div>
              <div>
                <span>Threat signature</span>
                <strong>{run?.threat_level || chamberMood}</strong>
              </div>
              <div>
                <span>Round limit</span>
                <strong>{run?.target_rounds || '--'} turns</strong>
              </div>
            </div>

            <div className={`interrogation-contract-rail ${contractAlert.tone}`}>
              <div className="diegetic-topline">
                <span>Contract cadence</span>
                <strong>{roundCount}/{run?.target_rounds || '--'} turns spent</strong>
              </div>
              <div className="contract-progress-track">
                <div style={{ width: `${contractProgress}%` }} />
              </div>
              <div className="contract-round-pips">
                {Array.from({ length: run?.target_rounds || 0 }, (_, index) => (
                  <span
                    key={`contract-pip-${index}`}
                    className={index < roundCount ? 'spent' : index === roundCount ? 'next' : ''}
                  />
                ))}
              </div>
              <div className="contract-rail-grid">
                <div className="contract-rail-card">
                  <span>Operator whisper</span>
                  <strong>{operatorWhisper.title}</strong>
                  <p>{operatorWhisper.detail}</p>
                </div>
                <div className={`contract-rail-card ${contractAlert.tone}`}>
                  <span>Risk profile</span>
                  <strong>{contractAlert.title}</strong>
                  <p>{contractAlert.detail}</p>
                </div>
                <div className="contract-rail-card doctrine">
                  <span>Contract doctrine</span>
                  <strong>{doctrine.headline}</strong>
                  <p>{doctrine.detail}</p>
                </div>
              </div>
            </div>

            <div className="wiretap-panel">
              <div className="diegetic-topline">
                <span>Wiretap feed</span>
                <strong>{latency ? `${latency.first_audio_ms}ms voice` : 'Listening for tells'}</strong>
              </div>
              <p className="wiretap-line">{latestSuspectLine}</p>
              <div className="wiretap-metrics">
                <div>
                  <span>LLM first text</span>
                  <strong>{latency?.first_text_ms ?? '--'} ms</strong>
                </div>
                <div>
                  <span>TTS first audio</span>
                  <strong>{latency?.first_audio_ms ?? '--'} ms</strong>
                </div>
              </div>
            </div>

            <div className="interrogation-recorder-panel centerpiece">
              <div className={`tape-recorder ${recording ? 'recording' : ''} ${thinking ? 'thinking' : ''}`}>
                <div className="tape-recorder-topline">
                  <span>Recorder deck</span>
                  <strong>{recording ? 'Live capture' : thinking ? 'Rewinding' : roomEntered ? 'Stand by' : 'Locked'}</strong>
                </div>
                <div className="tape-spools">
                  <div className="spool" />
                  <div className="spool" />
                </div>
                <button
                  type="button"
                  className={`hold-to-speak interrogation-ptt ${recording ? 'active' : ''}`}
                  onMouseDown={() => void startRecording()}
                  onMouseUp={stopRecording}
                  onMouseLeave={() => recording && stopRecording()}
                  onTouchStart={() => void startRecording()}
                  onTouchEnd={stopRecording}
                  disabled={!roomEntered || collapsed || Boolean(summary)}
                >
                  {recording ? 'Release to Submit' : entrySequence ? 'Chamber Sealing' : thinking || audioPlaying ? 'Cut Off Suspect' : roomEntered ? 'Hold to Speak' : 'Enter Room First'}
                </button>
                <div className="tape-noise-bar">
                  <div style={{ width: `${20 + micEnergy * 72}%` }} />
                </div>
                <div className="interrogation-recorder-footer">
                  <span>
                    {recording
                      ? 'Mic locked. Build one complete argument.'
                      : roomEntered
                        ? 'The tape only rewards structured pressure.'
                        : 'Open the briefing and enter the chamber to begin.'}
                  </span>
                  <strong>{summary ? 'Run Complete' : roomEntered ? 'Live Session' : 'Briefing Mode'}</strong>
                </div>
                <div className="interrogation-recorder-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={skipReply}
                    disabled={!roomEntered || (!thinking && !audioPlaying)}
                  >
                    Override Reply
                  </button>
                  <span>{thinking || audioPlaying ? 'Force the room back to your turn.' : 'The deck is waiting for your next strike.'}</span>
                </div>
              </div>
            </div>
          </section>

          <aside className="interrogation-telemetry-column">
            <section className="interrogation-notebook-panel">
              <div className="diegetic-topline">
                <span>Case notebook</span>
                <strong>{analysisLabel}</strong>
              </div>
              <div className="interrogation-notebook-pages">
                {notebook.slice(-8).map((entry) => (
                  <motion.div
                    key={entry.id}
                    className={`notebook-line ${entry.role}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                  >
                    <span>{entry.role === 'assistant' ? run?.suspect_name || 'Suspect' : 'You'}</span>
                    <p>{entry.text}</p>
                  </motion.div>
                ))}
                {partialTranscript ? (
                  <div className="notebook-line live">
                    <span>You</span>
                    <p>
                      {partialTranscript}
                      <i className="notebook-cursor" />
                    </p>
                  </div>
                ) : null}
              </div>
            </section>

            <section className="interrogation-detector-panel">
              <div className="diegetic-topline">
                <span>Lie detector</span>
                <strong>{defenseMeter}%</strong>
              </div>
              <svg viewBox="0 0 100 100" className="lie-detector-graph" aria-hidden="true">
                <polyline points={lieDetectorPath} />
              </svg>
              <div className="defense-meter-bar">
                <div style={{ width: `${defenseMeter}%` }} />
              </div>
              <div className="interrogation-analysis-grid">
                <div className="interrogation-analysis-card">
                  <span>Linkers</span>
                  <strong>{latestAnalysis?.advanced_linkers ?? 0}</strong>
                </div>
                <div className="interrogation-analysis-card">
                  <span>Examples</span>
                  <strong>{latestAnalysis?.example_count ?? 0}</strong>
                </div>
                <div className="interrogation-analysis-card">
                  <span>Words</span>
                  <strong>{latestAnalysis?.word_count ?? 0}</strong>
                </div>
              </div>
            </section>

            <section className="turn-insight-card">
              <div className="diegetic-topline">
                <span>Room readout</span>
                <strong>{pressureDelta >= 18 ? 'Breakthrough' : pressureDelta < 0 ? 'Punished' : 'Building'}</strong>
              </div>
              <p>
                {pressureDelta >= 18
                  ? 'That turn hit hard. You stacked logic, control, and tempo at the same time.'
                  : pressureDelta < 0
                    ? 'The suspect found space. Shorten the hesitation and return with one cleaner chain.'
                    : 'You moved the room, but the next answer still needs a sharper edge.'}
              </p>
            </section>
          </aside>
        </section>

        {!roomEntered && run ? (
          <motion.section
            className="interrogation-briefing-overlay"
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
          >
            <div className="interrogation-briefing-card">
              <div className="interrogation-briefing-copy">
                <span className="dispatch-tag">Mission briefing</span>
                <h2>{run.operation_name}</h2>
                <p>{run.mission_brief}</p>
              </div>
              <div className="interrogation-contract-strip">
                <div className="interrogation-contract-card current">
                  <span>Active contract</span>
                  <strong>{run.mode_label}</strong>
                  <p>{run.mode_blurb}</p>
                </div>
                <div className="interrogation-contract-card">
                  <span>Pressure rating</span>
                  <strong>{run.difficulty_label}</strong>
                  <p>{difficultyStars || '★★★'} chamber intensity with {run.target_rounds} live turns.</p>
                </div>
                <div className="interrogation-contract-card">
                  <span>Failure trigger</span>
                  <strong>{run.suspect_name}</strong>
                  <p>{doctrine.failure}</p>
                </div>
              </div>
              <div className="interrogation-briefing-grid">
                <div className="interrogation-briefing-stat">
                  <span>Case code</span>
                  <strong>{run.case_code}</strong>
                </div>
                <div className="interrogation-briefing-stat">
                  <span>Suspect</span>
                  <strong>{run.suspect_name}</strong>
                </div>
                <div className="interrogation-briefing-stat">
                  <span>Title</span>
                  <strong>{run.suspect_title}</strong>
                </div>
                <div className="interrogation-briefing-stat">
                  <span>Threat</span>
                  <strong>{run.threat_level}</strong>
                </div>
                <div className="interrogation-briefing-stat">
                  <span>Room class</span>
                  <strong>{run.difficulty_label}</strong>
                </div>
                <div className="interrogation-briefing-stat">
                  <span>Pressure rating</span>
                  <strong>{difficultyStars}</strong>
                </div>
              </div>
              <div className="interrogation-briefing-directives">
                {directives.map((directive) => (
                  <div key={`brief-${directive.key}`} className="briefing-directive-card">
                    <strong>{directive.label}</strong>
                    <p>{directive.detail}</p>
                    <span>+{directive.reward} credits</span>
                  </div>
                ))}
              </div>
              <div className="interrogation-briefing-actions">
                <button type="button" onClick={enterRoom}>Enter Chamber</button>
                <Link to="/games" className="secondary game-back-link">Back to Command</Link>
              </div>
            </div>
          </motion.section>
        ) : null}

        {summary ? (
          <motion.section
            className="interrogation-summary-panel overhaul"
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div className="interrogation-summary-hero">
              <span>{summary.mode_label || run?.mode_label || 'Contract Debrief'}</span>
              <strong>{summary.debrief_headline || summaryOutcome}</strong>
              <p>{summaryOutcome} with a live room rank of {summary.rank ?? operationRank}.</p>
            </div>

            <div className="interrogation-summary-debrief">
              <div className="summary-debrief-card">
                <span>What broke the room</span>
                <p>{summary.debrief_strength || 'Your cleanest turns created the only real openings.'}</p>
              </div>
              <div className="summary-debrief-card">
                <span>What held you back</span>
                <p>{summary.debrief_warning || 'The room recovered whenever your pressure lost shape.'}</p>
              </div>
              <div className="summary-debrief-card">
                <span>Next contract focus</span>
                <p>{summary.next_focus || 'Reload and push the next room with more structure.'}</p>
              </div>
            </div>

            <div className="interrogation-summary-stats">
              <div className="summary-stat-card">
                <span>Reward</span>
                <strong>{summary.reward} credits</strong>
              </div>
              <div className="summary-stat-card">
                <span>Directive bank</span>
                <strong>+{summary.bonus_reward ?? bonusReward}</strong>
              </div>
              <div className="summary-stat-card">
                <span>Max combo</span>
                <strong>x{summary.max_combo ?? combo}</strong>
              </div>
              <div className="summary-stat-card">
                <span>Phase breaks</span>
                <strong>{summary.phases_cleared ?? phaseBreaks}</strong>
              </div>
              <div className="summary-stat-card">
                <span>Directives</span>
                <strong>{summary.directives_cleared ?? directivesCompleted}</strong>
              </div>
              <div className="summary-stat-card">
                <span>Credits</span>
                <strong>{summary.profile?.credits ?? '--'}</strong>
              </div>
            </div>

            <div className="interrogation-summary-bottom">
              <div className="summary-directive-stack">
                <div className="diegetic-topline">
                  <span>Directive outcome</span>
                  <strong>{directivesCompleted}/{directives.length || 0} secured</strong>
                </div>
                <div className="summary-directive-list">
                  {directives.map((directive) => (
                    <div key={`summary-${directive.key}`} className={`summary-directive-item ${directive.completed ? 'completed' : ''}`}>
                      <div>
                        <strong>{directive.label}</strong>
                        <p>{directive.detail}</p>
                      </div>
                      <span>{directive.completed ? 'Secured' : `${directive.progress}/${directive.target}`}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="summary-transcript-card">
                <div className="diegetic-topline">
                  <span>Extraction tape</span>
                  <strong>{summary.rounds_completed ?? roundCount} turns archived</strong>
                </div>
                <p>{transcriptExcerpt}</p>
              </div>
            </div>

            <div className="interrogation-summary-actions">
              <button type="button" onClick={() => void bootRoom(run?.mode_key || requestedMode)}>Run Same Contract</button>
              <button type="button" className="secondary" onClick={() => void bootRoom()}>Load Fresh Briefing</button>
              <Link to="/games" className="secondary game-back-link">Change Contract</Link>
            </div>
          </motion.section>
        ) : null}
      </div>
    </div>
  );
}
