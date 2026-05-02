# English Learning System (ELS)

A local-first English learning web app for personal use.

## Features

- **Speaking Room** — AI-powered English conversation practice (WebSocket + ASR + TTS)
- **Daily News** — RSS-based English news with AI summaries
- **Vocabulary Images** — Visual word lookup via Wikimedia Commons
- **Diary** — English writing with AI feedback and corrections
- **Retell Game** — Story listening + retelling with level-based scoring

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 18+
- ffmpeg (for audio processing)
- Apple Silicon Mac recommended for local Qwen MLX speech models

### Setup

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your LLM API credentials

# 2. Backend
cd backend
python3.12 -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Frontend
cd ../frontend
npm install

# 4. Start (macOS / Linux)
cd ..
./start.sh

# 4. Start (Windows)
start.bat
```

Open http://127.0.0.1:5173

### Test

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -v
```

## Architecture

- **Backend**: FastAPI + SQLite + WebSocket + `mlx-qwen3-asr` + `Qwen3-TTS-MLX`
- **Frontend**: React + TypeScript + Vite + TanStack Query + Zustand
- **LLM**: OpenAI-compatible API (configurable base_url / api_key / model)
- **Budget**: 50 RMB/month cap with warning and blocking

## Speech Stack

- **ASR**: `Qwen/Qwen3-ASR-0.6B` via `mlx-qwen3-asr`
- **TTS**: `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` via the MLX fork `odiak/Qwen3-TTS-MLX`
- **Fallbacks**:
  - Whisper `base.en` / `small.en` for ASR
  - macOS `say` for TTS if the local Qwen voice stack is unavailable

The first run may download local speech weights from Hugging Face, so the first speaking or retell session can take noticeably longer than later ones.

On some macOS setups, `hf-xet` causes Hugging Face model downloads to stall or fail during the first Qwen speech-model pull. The included `start.sh` removes `hf-xet` before launch so downloads fall back to the standard HTTP path.

## Project Structure

```
ELS/
  backend/          # FastAPI application
    app/
      main.py       # App entry point
      config.py     # Pydantic settings
      database.py   # SQLite + SQLModel
      models/       # Database models
      schemas/      # Pydantic schemas
      routers/      # API routes (REST + WebSocket)
      services/     # Business logic
      prompts/      # LLM prompt templates
    tests/          # pytest tests
  frontend/         # React + Vite
    src/
      pages/        # Page components
      api/          # API client
      stores/       # Zustand state
  data/             # Local storage (DB, recordings, cache)
  .env              # Configuration
```
