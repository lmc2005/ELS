import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../api/client';
import ImmersiveStageCanvas from '../components/ImmersiveStageCanvas';
import { openPreferredAudioStream } from '../utils/audioInput';

interface Message {
  role: 'user' | 'assistant' | 'system';
  text: string;
}

interface Correction {
  original: string;
  better: string;
  reason: string;
}

interface NoteItem {
  type: string;
  content: string;
}

interface TurnMetrics {
  asr_ms: number;
  llm_ms: number;
  tts_ms: number;
  total_ms: number;
  first_audio_ms?: number;
  transcript_source?: string;
  streaming_reply?: boolean;
}

interface AssistantDraft {
  turnIndex: number;
  text: string;
}

type RoomPhase =
  | 'booting'
  | 'listening'
  | 'capturing'
  | 'transcribing'
  | 'thinking'
  | 'voicing'
  | 'assistant_speaking'
  | 'error';

const PHASE_META: Record<RoomPhase, { label: string; hint: string }> = {
  booting: {
    label: 'Preparing audio room',
    hint: 'Connecting the tutor and warming the microphone.',
  },
  listening: {
    label: 'Live and listening',
    hint: 'Just speak naturally. The room will auto-send after a short pause.',
  },
  capturing: {
    label: 'Hearing your turn',
    hint: 'Keep going. We are capturing your voice now.',
  },
  transcribing: {
    label: 'Transcribing',
    hint: 'Turning your speech into text locally with Qwen ASR.',
  },
  thinking: {
    label: 'Tutor is thinking',
    hint: 'The tutor is preparing a short, natural reply.',
  },
  voicing: {
    label: 'Generating voice',
    hint: 'Creating a low-latency British tutor voice for live chat.',
  },
  assistant_speaking: {
    label: 'Tutor is speaking',
    hint: 'You can interrupt and jump in if you want to steer the conversation.',
  },
  error: {
    label: 'Need another try',
    hint: 'Something went wrong, but the room is still ready for the next turn.',
  },
};

const SPEAKING_ASR_MODEL = 'qwen3_asr_mlx';
const AUTO_SEND_SILENCE_MS = 850;
const IDLE_PROMPT_MS = 3000;
const IDLE_PROMPT_COOLDOWN_MS = 9000;

export default function SpeakingRoom() {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [topic, setTopic] = useState('');
  const [mode, setMode] = useState('free_chat');
  const [focus, setFocus] = useState('general');
  const [messages, setMessages] = useState<Message[]>([]);
  const [corrections, setCorrections] = useState<Correction[]>([]);
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [manualNote, setManualNote] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [vadState, setVadState] = useState<'speaking' | 'silent'>('silent');
  const [micLevel, setMicLevel] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');
  const [roomPhase, setRoomPhase] = useState<RoomPhase>('booting');
  const [statusMessage, setStatusMessage] = useState(PHASE_META.booting.hint);
  const [turnMetrics, setTurnMetrics] = useState<TurnMetrics | null>(null);
  const [liveTranscript, setLiveTranscript] = useState('');
  const [activeMicLabel, setActiveMicLabel] = useState('');
  const [assistantDraft, setAssistantDraft] = useState<AssistantDraft | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const pcmSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const pcmProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const startTimeRef = useRef<number>(0);
  const activeTurnRecorderRef = useRef<MediaRecorder | null>(null);
  const activeTurnChunksRef = useRef<Blob[]>([]);
  const recorderMimeTypeRef = useRef('audio/webm');
  const noiseFloorRef = useRef(0.008);
  const assistantAudioRef = useRef<HTMLAudioElement | null>(null);
  const silenceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idlePromptTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const vadIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const speechDetectedRef = useRef(false);
  const turnInFlightRef = useRef(false);
  const assistantSpeakingRef = useRef(false);
  const turnClosingRef = useRef(false);
  const sessionLiveRef = useRef(false);
  const phaseRef = useRef<RoomPhase>('booting');
  const pcmStreamingRef = useRef(false);
  const lastEnglishWordAtRef = useRef(0);
  const lastIdlePromptAtRef = useRef(0);
  const waitingForAssistantAudioRef = useRef(false);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const activeAssistantTurnRef = useRef<number | null>(null);
  const pendingAssistantAudioRef = useRef<Map<number, string>>(new Map());
  const nextAssistantChunkRef = useRef(1);
  const assistantStreamDoneTurnRef = useRef<number | null>(null);
  const ignoredAssistantTurnsRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (!isRecording) return;
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [isRecording]);

  useEffect(() => {
    return () => {
      teardownAudioStack();
      disconnectRoom();
    };
  }, []);

  useEffect(() => {
    const node = messageListRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
  }, [messages, liveTranscript]);

  const createSession = useMutation({
    mutationFn: api.createSession,
    onSuccess: (data: any) => {
      setSessionId(data.id);
      connectWebSocket(data.id);
    },
  });

  const updateRoomPhase = (phase: RoomPhase, message?: string) => {
    phaseRef.current = phase;
    setRoomPhase(phase);
    setStatusMessage(message || PHASE_META[phase].hint);
    if (phase === 'listening') {
      scheduleIdlePrompt();
    } else {
      clearIdlePrompt();
    }
  };

  const resetAssistantStream = (turnIndex: number | null = null) => {
    activeAssistantTurnRef.current = turnIndex;
    assistantStreamDoneTurnRef.current = null;
    pendingAssistantAudioRef.current.clear();
    nextAssistantChunkRef.current = 1;
  };

  const maybeSetListeningAfterStream = () => {
    const activeTurn = activeAssistantTurnRef.current;
    const streamFinished = activeTurn !== null && assistantStreamDoneTurnRef.current === activeTurn;
    const queueEmpty = pendingAssistantAudioRef.current.size === 0;
    if (
      streamFinished &&
      queueEmpty &&
      !assistantAudioRef.current &&
      sessionLiveRef.current
    ) {
      waitingForAssistantAudioRef.current = false;
      turnInFlightRef.current = false;
      assistantSpeakingRef.current = false;
      activeAssistantTurnRef.current = null;
      assistantStreamDoneTurnRef.current = null;
      setAssistantDraft(null);
      updateRoomPhase('listening', 'Your turn. Jump in whenever you like.');
    }
  };

  const playNextAssistantChunk = () => {
    if (assistantAudioRef.current || assistantSpeakingRef.current) return;
    const activeTurn = activeAssistantTurnRef.current;
    if (activeTurn === null) return;
    if (ignoredAssistantTurnsRef.current.has(activeTurn)) return;

    const nextChunk = pendingAssistantAudioRef.current.get(nextAssistantChunkRef.current);
    if (!nextChunk) {
      maybeSetListeningAfterStream();
      return;
    }

    pendingAssistantAudioRef.current.delete(nextAssistantChunkRef.current);
    const audio = new Audio(`data:audio/wav;base64,${nextChunk}`);
    assistantAudioRef.current = audio;
    assistantSpeakingRef.current = true;
    turnInFlightRef.current = true;
    waitingForAssistantAudioRef.current = false;
    updateRoomPhase('assistant_speaking', 'Tutor is replying in real time...');

    audio.onended = () => {
      assistantAudioRef.current = null;
      assistantSpeakingRef.current = false;
      nextAssistantChunkRef.current += 1;

      if (pendingAssistantAudioRef.current.has(nextAssistantChunkRef.current)) {
        playNextAssistantChunk();
        return;
      }

      if (activeAssistantTurnRef.current === assistantStreamDoneTurnRef.current) {
        maybeSetListeningAfterStream();
        return;
      }

      updateRoomPhase('voicing', 'Tutor is continuing the reply...');
    };

    audio.play().catch(() => {
      assistantAudioRef.current = null;
      assistantSpeakingRef.current = false;
      nextAssistantChunkRef.current += 1;
      maybeSetListeningAfterStream();
    });
  };

  const enqueueAssistantAudioChunk = (turnIndex: number, chunkIndex: number, base64Audio: string) => {
    if (ignoredAssistantTurnsRef.current.has(turnIndex)) return;
    if (activeAssistantTurnRef.current !== turnIndex) {
      resetAssistantStream(turnIndex);
    }

    pendingAssistantAudioRef.current.set(chunkIndex, base64Audio);
    if (!assistantAudioRef.current) {
      playNextAssistantChunk();
    }
  };

  const connectWebSocket = (sid: number) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/speaking/sessions/${sid}/stream`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setErrorMessage('');
      sessionLiveRef.current = true;
      waitingForAssistantAudioRef.current = true;
      turnInFlightRef.current = true;
      updateRoomPhase('booting', 'Opening the mic and connecting the speaking room...');
      void startAudioStack();
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case 'vad.state':
          setVadState(data.state);
          break;
        case 'status':
          if (data.phase) {
            updateRoomPhase(data.phase as RoomPhase, data.message);
            if (data.phase === 'listening') turnInFlightRef.current = false;
            if (data.phase === 'transcribing' || data.phase === 'thinking' || data.phase === 'voicing') {
              turnInFlightRef.current = true;
            }
          }
          if (data.metrics) {
            setTurnMetrics(data.metrics);
          }
          break;
        case 'turn.metrics':
          setTurnMetrics(data.metrics);
          break;
        case 'transcript.final':
          markEnglishWord(data.text || '');
          setLiveTranscript('');
          setMessages((prev) => [...prev, { role: 'user', text: data.text }]);
          break;
        case 'transcript.partial':
          markEnglishWord(data.text || '');
          setLiveTranscript(data.text || '');
          break;
        case 'assistant.text':
          waitingForAssistantAudioRef.current = true;
          turnInFlightRef.current = true;
          if (!assistantSpeakingRef.current) updateRoomPhase('voicing', 'Tutor is preparing audio...');
          setMessages((prev) => [...prev, { role: 'assistant', text: data.text }]);
          break;
        case 'assistant.stream.start':
          waitingForAssistantAudioRef.current = true;
          turnInFlightRef.current = true;
          resetAssistantStream(data.turn_index ?? null);
          setAssistantDraft({ turnIndex: data.turn_index, text: '' });
          if (!assistantSpeakingRef.current) updateRoomPhase('thinking', 'Tutor is shaping the first reply chunk...');
          break;
        case 'assistant.text.chunk':
          if (ignoredAssistantTurnsRef.current.has(data.turn_index)) break;
          setAssistantDraft((prev) => {
            const incoming = String(data.text || '').trim();
            if (!incoming) return prev;
            if (!prev || prev.turnIndex !== data.turn_index) {
              return { turnIndex: data.turn_index, text: incoming };
            }
            return {
              turnIndex: prev.turnIndex,
              text: `${prev.text} ${incoming}`.replace(/\s+/g, ' ').trim(),
            };
          });
          if (!assistantSpeakingRef.current) updateRoomPhase('voicing', 'Tutor is opening the reply stream...');
          break;
        case 'assistant.audio.chunk':
          enqueueAssistantAudioChunk(data.turn_index, data.chunk_index, data.audio);
          break;
        case 'assistant.stream.end':
          if (ignoredAssistantTurnsRef.current.has(data.turn_index)) {
            if (activeAssistantTurnRef.current === data.turn_index) {
              resetAssistantStream(null);
            }
            setAssistantDraft(null);
            break;
          }
          assistantStreamDoneTurnRef.current = data.turn_index;
          {
            const finalText = String(data.text || '').trim()
              || (assistantDraft?.turnIndex === data.turn_index ? assistantDraft?.text ?? '' : '');
            if (finalText) {
              setMessages((current) => [...current, { role: 'assistant', text: finalText }]);
            }
          }
          setAssistantDraft(null);
          maybeSetListeningAfterStream();
          break;
        case 'assistant.audio':
          waitingForAssistantAudioRef.current = false;
          setAssistantDraft(null);
          stopAssistantAudio();
          playAssistantAudio(data.audio);
          break;
        case 'assistant.audio_error':
          waitingForAssistantAudioRef.current = false;
          turnInFlightRef.current = false;
          setErrorMessage(data.message || 'Tutor audio could not be generated.');
          updateRoomPhase('listening', 'Audio failed, but the room is still listening.');
          break;
        case 'correction':
          setCorrections(data.items);
          break;
        case 'auto_note':
          setNotes((prev) => [...prev, ...data.items]);
          break;
        case 'interrupt.ack':
          if (activeAssistantTurnRef.current !== null) {
            ignoredAssistantTurnsRef.current.add(activeAssistantTurnRef.current);
          }
          resetAssistantStream(null);
          setAssistantDraft(null);
          stopAssistantAudio();
          break;
        case 'error':
          setErrorMessage(data.message);
          turnInFlightRef.current = false;
          updateRoomPhase('error', data.message);
          window.setTimeout(() => {
            if (sessionLiveRef.current && !assistantSpeakingRef.current && !activeTurnRecorderRef.current) {
              updateRoomPhase('listening');
            }
          }, 1200);
          break;
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      sessionLiveRef.current = false;
      teardownAudioStack();
    };
  };

  const startAudioStack = async () => {
    try {
      const stream = await openPreferredAudioStream('auto');
      mediaStreamRef.current = stream;
      const track = stream.getAudioTracks()[0];
      setActiveMicLabel(track?.label || 'Selected microphone');
      const audioCtx = new AudioContext();
      audioContextRef.current = audioCtx;
      const analyser = audioCtx.createAnalyser();
      analyserRef.current = analyser;
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.76;
      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      const recorderOptions = pickRecorderOptions();
      recorderMimeTypeRef.current = recorderOptions?.mimeType || 'audio/webm';

      setIsRecording(true);
      setMicLevel(0);
      setElapsed(0);
      noiseFloorRef.current = 0.008;
      speechDetectedRef.current = false;
      turnInFlightRef.current = waitingForAssistantAudioRef.current;
      assistantSpeakingRef.current = false;
      turnClosingRef.current = false;
      startTimeRef.current = Date.now();
      startVadLoop();
      if (waitingForAssistantAudioRef.current) {
        updateRoomPhase('voicing', 'Tutor is preparing audio...');
      } else {
        updateRoomPhase('listening');
      }
    } catch (err: any) {
      const message = err?.name === 'NotAllowedError'
        ? 'Microphone permission is blocked. Please allow it and try again.'
        : 'We could not open the microphone for the speaking room.';
      setErrorMessage(message);
      updateRoomPhase('error', message);
    }
  };

  const startVadLoop = () => {
    if (vadIntervalRef.current) clearInterval(vadIntervalRef.current);
    vadIntervalRef.current = setInterval(() => {
      if (!analyserRef.current || !sessionLiveRef.current) return;

      const dataArray = new Uint8Array(analyserRef.current.fftSize);
      analyserRef.current.getByteTimeDomainData(dataArray);

      let sumSquares = 0;
      for (let i = 0; i < dataArray.length; i += 1) {
        const normalized = (dataArray[i] - 128) / 128;
        sumSquares += normalized * normalized;
      }
      const rms = Math.sqrt(sumSquares / dataArray.length);
      const visualLevel = Math.max(0, Math.min(100, Math.round(rms * 1800)));
      setMicLevel((previous) => Math.round(previous * 0.55 + visualLevel * 0.45));

      if (!activeTurnRecorderRef.current && !assistantSpeakingRef.current && !turnInFlightRef.current) {
        noiseFloorRef.current = noiseFloorRef.current * 0.94 + Math.min(rms, 0.025) * 0.06;
      }

      const threshold = Math.max(
        assistantSpeakingRef.current ? 0.036 : 0.015,
        noiseFloorRef.current * (assistantSpeakingRef.current ? 3.2 : 2.05),
      );
      const isSpeaking = rms >= threshold;

      if (assistantSpeakingRef.current && isSpeaking) {
        wsRef.current?.send(JSON.stringify({ type: 'interrupt' }));
        stopAssistantAudio();
        turnInFlightRef.current = false;
        updateRoomPhase('capturing', 'You interrupted the tutor. Go ahead.');
      }

      if (isSpeaking) {
        clearIdlePrompt();
        speechDetectedRef.current = true;
        setVadState('speaking');
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'vad.state', state: 'speaking' }));
        }
        if (!assistantSpeakingRef.current && !turnInFlightRef.current && !activeTurnRecorderRef.current) {
          startTurnCapture();
        }
        if (silenceTimeoutRef.current) {
          clearTimeout(silenceTimeoutRef.current);
          silenceTimeoutRef.current = null;
        }
      } else {
        setVadState('silent');
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'vad.state', state: 'silent' }));
        }
        if (activeTurnRecorderRef.current && !silenceTimeoutRef.current && !turnClosingRef.current) {
          silenceTimeoutRef.current = setTimeout(() => {
            stopTurnCapture();
            silenceTimeoutRef.current = null;
          }, AUTO_SEND_SILENCE_MS);
        }
      }
    }, 90);
  };

  const markEnglishWord = (text: string) => {
    if (/[A-Za-z]{2,}/.test(text)) {
      lastEnglishWordAtRef.current = Date.now();
      clearIdlePrompt();
    }
  };

  const clearIdlePrompt = () => {
    if (idlePromptTimeoutRef.current) {
      clearTimeout(idlePromptTimeoutRef.current);
      idlePromptTimeoutRef.current = null;
    }
  };

  const scheduleIdlePrompt = () => {
    clearIdlePrompt();
    if (!sessionLiveRef.current || phaseRef.current !== 'listening') return;
    idlePromptTimeoutRef.current = setTimeout(() => {
      const now = Date.now();
      const hasRecentEnglish = lastEnglishWordAtRef.current && now - lastEnglishWordAtRef.current < IDLE_PROMPT_MS;
      const onCooldown = lastIdlePromptAtRef.current && now - lastIdlePromptAtRef.current < IDLE_PROMPT_COOLDOWN_MS;
      if (
        hasRecentEnglish ||
        onCooldown ||
        activeTurnRecorderRef.current ||
        turnInFlightRef.current ||
        assistantSpeakingRef.current ||
        wsRef.current?.readyState !== WebSocket.OPEN
      ) {
        if (!hasRecentEnglish && onCooldown) {
          const wait = Math.max(500, IDLE_PROMPT_COOLDOWN_MS - (now - lastIdlePromptAtRef.current));
          idlePromptTimeoutRef.current = setTimeout(scheduleIdlePrompt, wait);
        }
        return;
      }
      lastIdlePromptAtRef.current = now;
      wsRef.current.send(JSON.stringify({ type: 'idle.prompt' }));
      updateRoomPhase('voicing', 'Tutor is gently keeping the conversation moving...');
    }, IDLE_PROMPT_MS);
  };

  const startTurnCapture = () => {
    if (!mediaStreamRef.current || activeTurnRecorderRef.current || turnInFlightRef.current) return;

    const recorderOptions = pickRecorderOptions();
    const recorder = recorderOptions
      ? new MediaRecorder(mediaStreamRef.current, recorderOptions)
      : new MediaRecorder(mediaStreamRef.current);

    recorderMimeTypeRef.current = recorder.mimeType || recorderOptions?.mimeType || 'audio/webm';
    activeTurnRecorderRef.current = recorder;
    activeTurnChunksRef.current = [];
    turnClosingRef.current = false;

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) activeTurnChunksRef.current.push(event.data);
    };

    recorder.onstop = () => {
      const blob = new Blob(activeTurnChunksRef.current, { type: recorderMimeTypeRef.current });
      activeTurnRecorderRef.current = null;
      activeTurnChunksRef.current = [];
      speechDetectedRef.current = false;
      turnClosingRef.current = false;

      if (!blob.size) {
        turnInFlightRef.current = false;
        if (!assistantSpeakingRef.current) updateRoomPhase('listening');
        return;
      }

      sendTurnBlob(blob);
    };

    recorder.start();
    startPcmStreaming();
    updateRoomPhase('capturing');
  };

  const stopTurnCapture = () => {
    if (silenceTimeoutRef.current) {
      clearTimeout(silenceTimeoutRef.current);
      silenceTimeoutRef.current = null;
    }
    const recorder = activeTurnRecorderRef.current;
    if (!recorder || recorder.state === 'inactive' || turnClosingRef.current) return;
    turnClosingRef.current = true;
    turnInFlightRef.current = true;
    stopPcmStreaming(true);
    updateRoomPhase('transcribing', 'Sending your audio turn for transcription...');
    try {
      recorder.requestData();
    } catch {
      // noop
    }
    window.setTimeout(() => {
      try {
        recorder.stop();
      } catch {
        turnClosingRef.current = false;
      }
    }, 20);
  };

  const sendTurnBlob = (blob: Blob) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) {
      turnInFlightRef.current = false;
      updateRoomPhase('error', 'The speaking socket disconnected. Please start again.');
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const base64 = (reader.result as string).split(',')[1];
      if (!base64) {
        turnInFlightRef.current = false;
        updateRoomPhase('error', 'Audio packaging failed. Please try again.');
        return;
      }
      wsRef.current?.send(JSON.stringify({
        type: 'audio.turn',
        data: base64,
        mimeType: recorderMimeTypeRef.current,
      }));
      updateRoomPhase('transcribing');
    };
    reader.readAsDataURL(blob);
  };

  const startPcmStreaming = () => {
    if (
      pcmStreamingRef.current ||
      !audioContextRef.current ||
      !mediaStreamRef.current ||
      wsRef.current?.readyState !== WebSocket.OPEN
    ) {
      return;
    }

    const audioCtx = audioContextRef.current;
    const source = audioCtx.createMediaStreamSource(mediaStreamRef.current);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    pcmSourceRef.current = source;
    pcmProcessorRef.current = processor;
    pcmStreamingRef.current = true;
    setLiveTranscript('');
    wsRef.current.send(JSON.stringify({ type: 'pcm.start' }));

    processor.onaudioprocess = (event) => {
      event.outputBuffer.getChannelData(0).fill(0);
      if (!pcmStreamingRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return;
      const input = event.inputBuffer.getChannelData(0);
      const downsampled = downsampleTo16k(input, audioCtx.sampleRate);
      if (!downsampled.length) return;
      wsRef.current.send(JSON.stringify({
        type: 'pcm.chunk',
        data: encodePcm16(downsampled),
      }));
    };

    source.connect(processor);
    processor.connect(audioCtx.destination);
  };

  const stopPcmStreaming = (sendEnd: boolean) => {
    if (sendEnd && pcmStreamingRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'pcm.end' }));
    }
    pcmStreamingRef.current = false;
    if (pcmProcessorRef.current) {
      pcmProcessorRef.current.disconnect();
      pcmProcessorRef.current.onaudioprocess = null;
    }
    if (pcmSourceRef.current) {
      pcmSourceRef.current.disconnect();
    }
    pcmProcessorRef.current = null;
    pcmSourceRef.current = null;
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

  const playAssistantAudio = (base64Audio: string) => {
    resetAssistantStream(null);
    const audio = new Audio(`data:audio/wav;base64,${base64Audio}`);
    assistantAudioRef.current = audio;
    assistantSpeakingRef.current = true;
    turnInFlightRef.current = true;
    updateRoomPhase('assistant_speaking');

    audio.onended = () => {
      assistantAudioRef.current = null;
      assistantSpeakingRef.current = false;
      turnInFlightRef.current = false;
      waitingForAssistantAudioRef.current = false;
      if (sessionLiveRef.current) updateRoomPhase('listening', 'Your turn. Jump in whenever you like.');
    };

    audio.play().catch(() => {
      assistantAudioRef.current = null;
      assistantSpeakingRef.current = false;
      turnInFlightRef.current = false;
      waitingForAssistantAudioRef.current = false;
      if (sessionLiveRef.current) updateRoomPhase('listening', 'Audio playback failed, but the room is still listening.');
    });
  };

  const stopAssistantAudio = () => {
    if (assistantAudioRef.current) {
      assistantAudioRef.current.pause();
      assistantAudioRef.current = null;
    }
    assistantSpeakingRef.current = false;
    waitingForAssistantAudioRef.current = false;
  };

  const teardownAudioStack = () => {
    if (silenceTimeoutRef.current) clearTimeout(silenceTimeoutRef.current);
    clearIdlePrompt();
    if (vadIntervalRef.current) clearInterval(vadIntervalRef.current);
    stopPcmStreaming(false);
    stopAssistantAudio();

    const recorder = activeTurnRecorderRef.current;
    if (recorder?.state && recorder.state !== 'inactive') {
      try {
        recorder.stop();
      } catch {
        // noop
      }
    }
    activeTurnRecorderRef.current = null;
    activeTurnChunksRef.current = [];

    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
    audioContextRef.current?.close();
    audioContextRef.current = null;
    analyserRef.current = null;
    turnInFlightRef.current = false;
    waitingForAssistantAudioRef.current = false;
    speechDetectedRef.current = false;
    turnClosingRef.current = false;
    resetAssistantStream(null);
    setIsRecording(false);
    setMicLevel(0);
    setLiveTranscript('');
    setActiveMicLabel('');
    setAssistantDraft(null);
  };

  const disconnectRoom = () => {
    sessionLiveRef.current = false;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'session.end' }));
      wsRef.current.close();
    }
    wsRef.current = null;
  };

  const handleStart = () => {
    setMessages([]);
    setCorrections([]);
    setNotes([]);
    setElapsed(0);
    setErrorMessage('');
    setTurnMetrics(null);
    setLiveTranscript('');
    setAssistantDraft(null);
    lastEnglishWordAtRef.current = 0;
    lastIdlePromptAtRef.current = 0;
    ignoredAssistantTurnsRef.current.clear();
    resetAssistantStream(null);
    updateRoomPhase('booting');
    createSession.mutate({
      topic,
      mode,
      coach_focus: focus,
      asr_model: SPEAKING_ASR_MODEL,
    });
  };

  const handleEnd = () => {
    if (sessionId) {
      void api.endSession(sessionId).catch(() => undefined);
    }
    ignoredAssistantTurnsRef.current.clear();
    resetAssistantStream(null);
    setAssistantDraft(null);
    disconnectRoom();
    teardownAudioStack();
    setIsConnected(false);
    setSessionId(null);
    updateRoomPhase('booting');
  };

  const handleSaveNote = () => {
    const content = manualNote.trim();
    if (!content || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'note.update', content }));
    setNotes((prev) => [...prev, { type: 'manual', content }]);
    setManualNote('');
  };

  const handleSendTurn = () => {
    setErrorMessage('');
    if (activeTurnRecorderRef.current) {
      stopTurnCapture();
      return;
    }
    if (!speechDetectedRef.current) {
      setErrorMessage('Start speaking first, then send the turn when you pause.');
    }
  };

  const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`;
  const formatMs = (value?: number) => (typeof value === 'number' ? `${Math.round(value)} ms` : '--');

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

  const statusMeta = PHASE_META[roomPhase];
  const showTutorMotion = roomPhase === 'thinking' || roomPhase === 'voicing' || roomPhase === 'assistant_speaking';
  const transcriptModeLabel = turnMetrics?.transcript_source === 'full_asr'
    ? 'Live preview + full ASR'
    : turnMetrics?.transcript_source === 'stream_fallback'
      ? 'Low-latency live transcript'
      : 'Live preview + full ASR';
  const exchangeCount = messages.filter((message) => message.role !== 'system').length;
  const userTurns = messages.filter((message) => message.role === 'user').length;
  const tutorTurns = messages.filter((message) => message.role === 'assistant').length;
  const roomEnergy = Math.min(100, Math.round(micLevel * 0.42 + exchangeCount * 8 + (assistantDraft ? 12 : 0)));
  const flowStreak = Math.max(1, Math.ceil(exchangeCount / 2));
  const captureProgress = roomPhase === 'capturing'
    ? Math.min(100, Math.max(18, micLevel + 12))
    : roomPhase === 'thinking' || roomPhase === 'voicing'
      ? 76
      : roomPhase === 'assistant_speaking'
        ? 94
        : 22;
  const livePreviewWords = liveTranscript.trim() ? liveTranscript.trim().split(/\s+/).length : 0;
  const assistantDraftWords = assistantDraft?.text.trim() ? assistantDraft.text.trim().split(/\s+/).length : 0;
  const callCue = roomPhase === 'capturing'
    ? 'Keep the thread going. The room is tracking your voice lane now.'
    : roomPhase === 'thinking'
      ? 'The tutor is shaping a follow-up around your last idea.'
      : roomPhase === 'voicing'
        ? 'Reply stream is opening. Stay ready to jump back in.'
        : roomPhase === 'assistant_speaking'
          ? 'Treat this like a real call. You can interrupt to steer the topic.'
          : 'Open with one simple view, then add an example from your life.';
  const sceneSeeds = [
    {
      label: 'Weekend rewind',
      topic: 'weekend plans, something unexpected, and how it felt',
      focus: 'fluency',
      mode: 'topic_chat',
      copy: 'Easy warm-up that naturally turns into a short story.',
    },
    {
      label: 'Film hot take',
      topic: 'a film or series you loved or hated recently',
      focus: 'ideas',
      mode: 'topic_chat',
      copy: 'Great for opinions, reasons, and push-back.',
    },
    {
      label: 'Travel moment',
      topic: 'a trip, a place, or a transport mishap',
      focus: 'vocabulary',
      mode: 'topic_chat',
      copy: 'Good for description, detail, and sequence words.',
    },
    {
      label: 'Open free chat',
      topic: '',
      focus: 'general',
      mode: 'free_chat',
      copy: 'Jump in cold and let the tutor drive the first turn.',
    },
  ] as const;
  const callTactics = [
    {
      tag: 'Push',
      title: 'Keep the thread moving',
      copy: roomPhase === 'capturing'
        ? 'Stay on the same idea for one more sentence before pausing.'
        : 'When the tutor replies, answer with an opinion and one concrete example.',
    },
    {
      tag: 'Pivot',
      title: 'Change direction naturally',
      copy: assistantSpeakingRef.current
        ? 'Interrupt with a new angle if the topic drifts somewhere dull.'
        : 'Use “Actually”, “To be honest”, or “That reminds me...” to turn the topic.',
    },
    {
      tag: 'Clarify',
      title: 'Recover without panic',
      copy: livePreviewWords > 0
        ? 'If the preview is forming, slow down and land the main noun or verb cleanly.'
        : 'If you blank, restate the idea with simpler words instead of stopping.',
    },
    {
      tag: 'Depth',
      title: 'Sound more like a real person',
      copy: focus === 'ideas'
        ? 'Add a reason, a feeling, or a tiny personal story.'
        : 'Add contrast, comparison, or a quick memory to avoid one-line replies.',
    },
  ];
  const flowBoard = [
    {
      step: 'Open',
      state: roomPhase === 'listening' ? 'active' : userTurns > 0 ? 'done' : 'idle',
      copy: 'Start with one view, one memory, or one short answer you can grow.',
    },
    {
      step: 'Build',
      state: roomPhase === 'capturing' || roomPhase === 'transcribing' ? 'active' : exchangeCount >= 2 ? 'done' : 'idle',
      copy: 'Add one detail that makes the idea feel lived rather than textbook.',
    },
    {
      step: 'Barge',
      state: roomPhase === 'assistant_speaking' ? 'active' : tutorTurns > 0 ? 'done' : 'idle',
      copy: 'If the tutor goes long, jump in and steer the call the way real people do.',
    },
    {
      step: 'Lock',
      state: notes.length > 0 || corrections.length > 0 ? 'done' : 'idle',
      copy: 'Drop useful phrases into notes so the conversation leaves something behind.',
    },
  ];

  if (!sessionId || !isConnected) {
    return (
      <div className="page-stack">
        <section className="immersive-stage speaking-call-stage">
          <ImmersiveStageCanvas variant="speaking" phase="booting" energy={42} secondary={58} />
          <div className="immersive-stage-overlay speaking-stage-overlay">
            <div className="stage-kicker-group">
              <div className="eyebrow">Speaking Practice</div>
              <div className="status-strip">
                <div className="status-pill quiet">3s contextual idle</div>
                <div className="status-pill quiet">Live preview + full ASR</div>
                <div className="status-pill quiet">Chunked British reply</div>
              </div>
            </div>
            <div className="speaking-stage-copy">
              <h1 className="page-title">Walk into a voice room that feels alive</h1>
              <p className="page-subtitle">
                No transcript wall, no exam form feeling. This room behaves more like a live call with a British tutor who listens,
                nudges, interrupts, and keeps the thread moving.
              </p>
            </div>
            <div className="speaking-stage-hud">
              <div className="hud-pill">
                <span>Tutor mode</span>
                <strong>Conversational British coach</strong>
              </div>
              <div className="hud-pill">
                <span>Core loop</span>
                <strong>Speak, pause, hear the reply stream</strong>
              </div>
            </div>
          </div>
        </section>

        <section className="setup-grid speaking-launch-grid">
          <div className="surface">
            <h2 className="section-title">Session setup</h2>
            <div className="field-stack">
              <div>
                <label>Mode</label>
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  <option value="free_chat">Free Chat</option>
                  <option value="topic_chat">Topic Chat</option>
                </select>
              </div>
              <div>
                <label>Topic</label>
                <input
                  type="text"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="travel, films, tech, uni life..."
                />
              </div>
              <div>
                <label>Coach focus</label>
                <select value={focus} onChange={(e) => setFocus(e.target.value)}>
                  <option value="general">General</option>
                  <option value="fluency">Fluency</option>
                  <option value="grammar">Grammar</option>
                  <option value="vocabulary">Vocabulary</option>
                  <option value="pronunciation">Pronunciation</option>
                  <option value="ideas">Ideas</option>
                </select>
              </div>
            </div>
            <p className="surface-copy">
              Microphone and speech recognition are now handled automatically so the room opens faster and stays focused on the conversation.
            </p>
            <div className="scene-seed-grid" style={{ marginTop: 18 }}>
              {sceneSeeds.map((seed) => (
                <button
                  key={seed.label}
                  type="button"
                  className={`scene-seed-card ${topic === seed.topic && mode === seed.mode ? 'active' : ''}`}
                  onClick={() => {
                    setTopic(seed.topic);
                    setFocus(seed.focus);
                    setMode(seed.mode);
                  }}
                >
                  <span className="scene-seed-label">{seed.label}</span>
                  <strong>{seed.copy}</strong>
                </button>
              ))}
            </div>
            <button onClick={handleStart} disabled={createSession.isPending} style={{ marginTop: 18 }}>
              {createSession.isPending ? 'Opening room...' : 'Start speaking room'}
            </button>
          </div>

          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Conversation sparks</h2>
              <span className="section-tag">Call-ready</span>
            </div>
            <div className="speaking-feature-list">
              <div className="feature-line">
                <span className="feature-dot">1</span>
                <div>
                  <strong>Voice-first stage</strong>
                  <p className="surface-copy">The tutor feels present on screen, but the spoken text stays out of your way.</p>
                </div>
              </div>
              <div className="feature-line">
                <span className="feature-dot">2</span>
                <div>
                  <strong>Real call rhythm</strong>
                  <p className="surface-copy">A short silence hands the turn over, and you can cut back in mid-reply.</p>
                </div>
              </div>
              <div className="feature-line">
                <span className="feature-dot">3</span>
                <div>
                  <strong>Coach memory</strong>
                  <p className="surface-copy">Corrections and notes remain visible so the room teaches without turning into a chat log.</p>
                </div>
              </div>
            </div>
            <div className="signal-grid" style={{ marginTop: 16 }}>
              <div className="signal-card">
                <span>Idle cue</span>
                <strong>3s</strong>
              </div>
              <div className="signal-card">
                <span>Interrupts</span>
                <strong>Live</strong>
              </div>
              <div className="signal-card">
                <span>Tutor accent</span>
                <strong>British</strong>
              </div>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <section className="immersive-stage speaking-call-stage live">
        <ImmersiveStageCanvas
          variant="speaking"
          phase={roomPhase}
          energy={roomEnergy}
          secondary={assistantSpeakingRef.current ? 84 : Math.max(micLevel, captureProgress)}
        />
        <div className="immersive-stage-overlay speaking-live-overlay">
          <div className="call-topline">
            <div>
              <div className="eyebrow">Speaking Room Live</div>
              <h1 className="page-title">{topic ? topic : 'Open English conversation'}</h1>
              <p className="page-subtitle">
                {mode === 'topic_chat' ? 'Topic-led conversation' : 'Free-flowing tutor chat'} · Focus on {focus} · {transcriptModeLabel}
              </p>
            </div>
            <div className="room-actions">
              <div className="timer-chip">{formatTime(elapsed)}</div>
              <div className="timer-chip">Flow x{flowStreak}</div>
              <div className="timer-chip">{userTurns} user turns</div>
              <button
                className="secondary"
                onClick={handleSendTurn}
                disabled={roomPhase === 'transcribing' || roomPhase === 'thinking' || roomPhase === 'voicing'}
              >
                Send turn now
              </button>
              <button className="danger" onClick={handleEnd}>End session</button>
            </div>
          </div>

          <div className="call-presence-grid">
            <div className="presence-column">
              <div className="presence-chip">{statusMeta.label}</div>
              <div className="presence-name">Tutor channel</div>
              <div className="presence-copy">
                {assistantSpeakingRef.current
                  ? 'Tutor is speaking now'
                  : assistantDraftWords > 0
                    ? 'Tutor is shaping the next follow-up'
                    : 'Tutor is listening for your next move'}
              </div>
            </div>
            <div className="call-core-status">
              <div className="call-core-ring">
                <span>{roomPhase === 'assistant_speaking' ? 'LIVE' : vadState === 'speaking' ? 'YOU' : 'CALL'}</span>
              </div>
              <div className="call-core-copy">
                <strong>{callCue}</strong>
                <span>{statusMessage}</span>
              </div>
            </div>
            <div className="presence-column align-end">
              <div className="presence-chip">{vadState === 'speaking' ? 'Voice detected' : 'Waiting on your cue'}</div>
              <div className="presence-name">Your lane</div>
              <div className="presence-copy">
                {livePreviewWords > 0
                  ? `${livePreviewWords} preview words are forming in the background.`
                  : 'Speak naturally. The room will hand off after a short pause.'}
              </div>
            </div>
          </div>

          <div className="call-lanes">
            <div className="audio-lane">
              <div className="audio-lane-head">
                <span>Tutor activity</span>
                <strong>{assistantSpeakingRef.current ? 'Speaking' : assistantDraftWords > 0 ? 'Preparing reply' : 'Listening'}</strong>
              </div>
              <div className="audio-lane-bars">
                {Array.from({ length: 16 }).map((_, index) => (
                  <span
                    key={`tutor-${index}`}
                    className={`audio-lane-bar ${assistantSpeakingRef.current ? 'active' : assistantDraftWords > 0 ? 'warm' : ''}`}
                    style={{ height: `${18 + ((assistantDraftWords * 5 + index * 9 + roomEnergy) % 54)}px` }}
                  />
                ))}
              </div>
            </div>
            <div className="audio-lane">
              <div className="audio-lane-head">
                <span>Your activity</span>
                <strong>{vadState === 'speaking' ? 'In flow' : 'Ready to jump in'}</strong>
              </div>
              <div className="audio-lane-bars">
                {Array.from({ length: 16 }).map((_, index) => (
                  <span
                    key={`user-${index}`}
                    className={`audio-lane-bar ${vadState === 'speaking' ? 'active user' : 'user'}`}
                    style={{ height: `${16 + ((micLevel + index * 7) % 58)}px` }}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="speaking-hud-band">
        <div className="surface hud-panel">
          <div className="section-head">
            <h2 className="section-title">Call momentum</h2>
            <span className="section-tag">No transcript wall</span>
          </div>
          <div className="metrics-strip">
            <div className="metric-box">
              <span>Room energy</span>
              <strong>{roomEnergy}%</strong>
            </div>
            <div className="metric-box">
              <span>User turns</span>
              <strong>{userTurns}</strong>
            </div>
            <div className="metric-box">
              <span>Tutor turns</span>
              <strong>{tutorTurns}</strong>
            </div>
            <div className="metric-box">
              <span>Mic lane</span>
              <strong>{micLevel}%</strong>
            </div>
          </div>
        </div>

        <div className="surface hud-panel">
          <div className="section-head">
            <h2 className="section-title">Coach cues</h2>
            <span className="section-tag">{focus}</span>
          </div>
          <div className="speaking-cue-stack">
            <div className="cue-line">
              <span className="cue-index">01</span>
              <div>
                <strong>Current moment</strong>
                <p className="surface-copy">{callCue}</p>
              </div>
            </div>
            <div className="cue-line">
              <span className="cue-index">02</span>
              <div>
                <strong>Mic source</strong>
                <p className="surface-copy">{activeMicLabel || 'Mac microphone preferred'}.</p>
              </div>
            </div>
            <div className="cue-line">
              <span className="cue-index">03</span>
              <div>
                <strong>Coach behaviour</strong>
                <p className="surface-copy">The tutor can nudge after 3 seconds of silence and you can barge in while it is speaking.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {errorMessage && <div className="error-banner">{errorMessage}</div>}

      <section className="call-tactic-grid">
        {callTactics.map((tactic) => (
          <div key={tactic.tag} className="surface tactic-surface">
            <div className="tactic-tag">{tactic.tag}</div>
            <strong>{tactic.title}</strong>
            <p className="surface-copy">{tactic.copy}</p>
          </div>
        ))}
      </section>

      <section className="speaking-layout">
        <div className="surface speaking-director-surface">
          <div className="section-head">
            <h2 className="section-title">Flow board</h2>
            <span className="section-tag">Voice-only presentation</span>
          </div>
          <div className="phase-track">
            {flowBoard.map((item) => (
              <div key={item.step} className={`phase-step-card ${item.state}`}>
                <span>{item.step}</span>
                <strong>{item.state === 'done' ? 'Locked' : item.state === 'active' ? 'Live' : 'Stand by'}</strong>
                <p>{item.copy}</p>
              </div>
            ))}
          </div>
          <div className="speaking-cue-stack" style={{ marginTop: 18 }}>
            <div className="cue-line">
              <span className="cue-index">A</span>
              <div>
                <strong>What you see</strong>
                <p className="surface-copy">The interface keeps the spoken words off-screen so your attention stays on timing, tone, and turn-taking.</p>
              </div>
            </div>
            <div className="cue-line">
              <span className="cue-index">B</span>
              <div>
                <strong>Best move</strong>
                <p className="surface-copy">
                  {vadState === 'speaking'
                    ? 'Stay with the same idea and add one concrete example before pausing.'
                    : assistantSpeakingRef.current
                      ? 'Listen for the opening and cut in naturally if you want to redirect the topic.'
                      : 'Start with one opinion, one memory, or one short story in English.'}
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="side-column">
          {turnMetrics && (
            <div className="surface latency-surface">
              <div className="section-head">
                <h2 className="section-title">Turn speed</h2>
                <span className="section-tag">{transcriptModeLabel}</span>
              </div>
              <div className="metrics-strip">
                <div className="metric-box">
                  <span>ASR</span>
                  <strong>{formatMs(turnMetrics.asr_ms)}</strong>
                </div>
                <div className="metric-box">
                  <span>LLM</span>
                  <strong>{formatMs(turnMetrics.llm_ms)}</strong>
                </div>
                <div className="metric-box">
                  <span>TTS</span>
                  <strong>{formatMs(turnMetrics.tts_ms)}</strong>
                </div>
                <div className="metric-box">
                  <span>First voice</span>
                  <strong>{formatMs(turnMetrics.first_audio_ms)}</strong>
                </div>
                <div className="metric-box">
                  <span>Total</span>
                  <strong>{formatMs(turnMetrics.total_ms)}</strong>
                </div>
              </div>
            </div>
          )}

          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Corrections</h2>
              <span className="section-tag">{corrections.length}</span>
            </div>
            {corrections.length === 0 ? (
              <p className="empty-copy">The tutor will surface the most useful fixes here without exposing the full spoken transcript on screen.</p>
            ) : (
              <div className="stack-list">
                {corrections.map((correction, index) => (
                  <div key={index} className="feedback-row">
                    <div className="feedback-before">{correction.original}</div>
                    <div className="feedback-after">{correction.better}</div>
                    <div className="feedback-reason">{correction.reason}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="surface">
            <div className="section-head">
              <h2 className="section-title">Notes</h2>
              <span className="section-tag">{notes.length}</span>
            </div>
            <textarea
              value={manualNote}
              onChange={(e) => setManualNote(e.target.value)}
              placeholder="Capture phrases, ideas, or mistakes worth revisiting..."
              style={{ minHeight: 110, marginBottom: 12 }}
            />
            <button onClick={handleSaveNote}>Save note</button>
            <div className="stack-list" style={{ marginTop: 14 }}>
              {notes.map((noteItem, index) => (
                <div key={index} className="note-chip">
                  <strong>{noteItem.type}</strong>
                  <span>{noteItem.content}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
