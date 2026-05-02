from pathlib import Path
import numpy as np


class VADService:
    def __init__(self):
        self._model = None
        self.sample_rate = 16000
        self.silence_threshold = 0.5
        self.speech_threshold = 0.9

    @property
    def model(self):
        if self._model is None:
            try:
                from silero_vad import load_silero_vad
                import torch
                self._model = load_silero_vad()
            except Exception:
                self._model = None
        return self._model

    def is_speech(self, audio_data: bytes, sample_rate: int = 16000) -> bool:
        """Check if audio chunk contains speech using Silero VAD."""
        model = self.model
        if model is None:
            # Fallback to energy-based detection
            return self._energy_based_detect(audio_data)
        try:
            import torch
            # Convert raw PCM bytes to float32 tensor
            audio_f = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(audio_f).unsqueeze(0)
            prob = model(tensor, sample_rate).item()
            return prob > self.speech_threshold
        except Exception:
            return self._energy_based_detect(audio_data)

    def _energy_based_detect(self, audio_data: bytes) -> bool:
        """Fallback: simple RMS energy detection."""
        if len(audio_data) < 2:
            return False
        data = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(data ** 2))
        return rms > 200  # threshold for 16-bit PCM


vad_service = VADService()
