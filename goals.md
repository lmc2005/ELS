# English Learning System 開發方案

## 1. 項目目標

開發一個面向個人/內測使用的英文學習 Web App，核心功能包括：

- 外教式口語練習
- 每日英文新聞
- 英文詞彙圖片搜索
- 英文日記批改
- 故事復述闖關遊戲

系統使用 Python 作為後端主語言，前端使用 Web 技術。資料默認本地保存。每月總成本控制在 **50 RMB 以內**，每天最多支援 **4 小時使用**。

LLM 不在本機直接跑模型，而是採用 **OpenAI-compatible API 調用方式**：用戶在 Settings 中填入 `base_url`、`api_key`、`model_name` 後，後端通過統一 LLM client 調用服務。此形式與 cc switch 類似，方便切換不同 API 服務商或本地網關。

## 2. 技術棧

### 後端

- Python 3.12
- FastAPI
- Uvicorn
- WebSocket
- SQLModel 或 SQLAlchemy
- SQLite
- Alembic
- Pydantic
- APScheduler
- ffmpeg
- faster-whisper
- webrtcvad 或 Silero VAD
- httpx
- OpenAI-compatible SDK/client

### 前端

- React
- Vite
- TypeScript
- React Router
- TanStack Query
- Zustand
- Web Audio API
- MediaRecorder API
- WebSocket client

### 本地語音組件

- ASR：faster-whisper，默認模型 `base.en`
- VAD：前端 RMS + 後端 VAD 雙重判斷
- TTS：Piper，默認選擇 `en_GB` voice
- 音訊處理：ffmpeg

### 外部 API

- LLM：OpenAI-compatible API
- 新聞：RSS/free feeds，默認不使用付費 NewsAPI
- 圖片：Wikimedia Commons API，Pexels/Pixabay 作可選免費配置

## 3. 工程目錄

```text
english-learning-system/
  backend/
    app/
      main.py
      config.py
      database.py
      models/
        user.py
        speaking.py
        diary.py
        news.py
        vocab.py
        story.py
        usage.py
      schemas/
        speaking.py
        diary.py
        news.py
        vocab.py
        story.py
        settings.py
      routers/
        speaking.py
        diary.py
        news.py
        vocab.py
        story.py
        settings.py
        history.py
      services/
        llm_client.py
        asr_service.py
        tts_service.py
        vad_service.py
        recording_service.py
        budget_service.py
        news_service.py
        vocab_image_service.py
        diary_service.py
        story_service.py
      prompts/
        speaking_teacher.md
        diary_feedback.md
        story_generation.md
        story_grading.md
        news_summary.md
      migrations/
    tests/
  frontend/
    src/
      api/
      components/
      pages/
        Dashboard.tsx
        SpeakingRoom.tsx
        NewsPage.tsx
        VocabImagePage.tsx
        DiaryPage.tsx
        RetellGamePage.tsx
        HistoryPage.tsx
        SettingsPage.tsx
      stores/
      hooks/
      types/
  data/
    db/
      app.db
    recordings/
    cache/
      news/
      images/
    exports/
  .env.example
  README.md
```

## 4. 環境配置

`.env.example`：

```env
APP_HOST=127.0.0.1
APP_PORT=8000
DATABASE_URL=sqlite:///data/db/app.db

LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=60

MONTHLY_BUDGET_RMB=50
BUDGET_WARNING_RMB=45

ASR_MODEL=base.en
TTS_PROVIDER=piper
TTS_VOICE=en_GB

NEWS_FETCH_TIME=07:30
IMAGE_CACHE_DAYS=30
```

Settings 頁面必須支持修改：

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `ASR_MODEL`
- `TTS_VOICE`
- `MONTHLY_BUDGET_RMB`

API key 只保存在本地 SQLite 或本地配置文件，不上傳雲端。

## 5. LLM Client 設計

建立統一 `LLMClient`，所有 AI 文本任務都通過它調用。

接口：

```python
class LLMClient:
    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        temperature: float = 0.4,
    ) -> dict:
        ...

    async def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
    ) -> str:
        ...
```

要求：

- 支持 OpenAI-compatible `/chat/completions`
- 從 Settings 或 `.env` 讀取 `base_url`、`api_key`、`model`
- 每次調用記錄 usage 到 `usage_events`
- 如果 provider 不返回 token usage，按字符數粗估
- 調用前檢查 `budget_service`
- 超過 50 RMB 月預算時阻止付費調用
- 失敗時返回可讀錯誤，不讓整個 session 崩潰

## 6. 資料模型

### speaking_sessions

字段：

- `id`
- `topic`
- `mode`: `free_chat | topic_chat`
- `coach_focus`: `fluency | grammar | vocabulary | pronunciation | ideas | general`
- `started_at`
- `ended_at`
- `summary`
- `cost_rmb`
- `recording_path`

### utterances

字段：

- `id`
- `session_id`
- `role`: `user | assistant`
- `text`
- `audio_path`
- `started_at`
- `ended_at`
- `confidence`

### notes

字段：

- `id`
- `session_id`
- `source`: `manual | ai`
- `type`: `phrase | correction | grammar | idea | vocabulary`
- `content`
- `created_at`

### usage_events

字段：

- `id`
- `provider`
- `feature`
- `model`
- `input_tokens`
- `output_tokens`
- `audio_seconds`
- `estimated_cost_rmb`
- `created_at`

### diary_entries

字段：

- `id`
- `date`
- `original_text`
- `better_version`
- `feedback_json`
- `created_at`

### news_articles

字段：

- `id`
- `category`: `ai_tech | current_affairs`
- `title`
- `source`
- `url`
- `published_at`
- `summary`
- `keywords_json`
- `difficulty`

### vocab_searches

字段：

- `id`
- `word`
- `results_json`
- `created_at`

### stories

字段：

- `id`
- `level`
- `title`
- `story_text`
- `audio_path`
- `key_points_json`
- `difficulty_tags_json`

### retell_attempts

字段：

- `id`
- `story_id`
- `audio_path`
- `transcript`
- `score_total`
- `score_json`
- `passed`
- `created_at`

## 7. API 設計

### Settings

- `GET /api/settings`
- `PUT /api/settings`
- `POST /api/settings/test-llm`

`test-llm` 用於測試 base URL、API key、model 是否可用。

### Speaking

- `POST /api/speaking/sessions`
- `WS /api/speaking/sessions/{session_id}/stream`
- `POST /api/speaking/sessions/{session_id}/end`
- `GET /api/speaking/sessions/{session_id}`

WebSocket client events：

```json
{ "type": "audio.chunk", "data": "base64_audio" }
{ "type": "turn.end" }
{ "type": "note.update", "content": "..." }
{ "type": "session.end" }
```

WebSocket server events：

```json
{ "type": "vad.state", "state": "speaking|silent" }
{ "type": "transcript.final", "text": "..." }
{ "type": "assistant.text", "text": "..." }
{ "type": "assistant.audio", "audio": "base64_audio" }
{ "type": "correction", "items": [...] }
{ "type": "auto_note", "items": [...] }
{ "type": "error", "message": "..." }
```

### Diary

- `POST /api/diary`
- `GET /api/diary`
- `GET /api/diary/{id}`

### News

- `GET /api/news?category=ai_tech|current_affairs`
- `POST /api/news/refresh`
- `POST /api/news/interactions`

### Vocab Image

- `GET /api/vocab/images?word=apple`

### Story Retell

- `POST /api/story/start`
- `POST /api/story/{story_id}/attempt`
- `GET /api/story/progress`
- `GET /api/story/levels`

## 8. 口語練習流程

1. 用戶進入 Speaking 頁面。
2. 選擇模式：
   - Free Chat
   - Topic Chat
3. 可輸入 topic，例如：
   - travel
   - AI technology
   - movies
   - daily life
4. 前端開始錄音。
5. 前端使用 Web Audio API 檢測音量。
6. 用戶說話後，如果連續 2 秒無聲，自動發送 `turn.end`。
7. 後端將本輪音訊保存為臨時文件。
8. ASR 轉寫英文文本。
9. 轉寫結果存入 `utterances`。
10. 後端構造上下文，調用 LLM API。
11. LLM 返回 JSON：
    ```json
    {
      "reply_text": "...",
      "corrections": [
        {
          "original": "...",
          "better": "...",
          "reason": "..."
        }
      ],
      "note_suggestions": [
        {
          "type": "phrase",
          "content": "..."
        }
      ],
      "follow_up_question": "..."
    }
    ```
12. `reply_text` 送入 Piper TTS。
13. 前端播放 AI 聲音。
14. 糾錯與 note 顯示在側邊欄。
15. session 結束時合成完整錄音，生成 session summary。

口語 prompt 原則：

- AI 是 British English 外教。
- 對話應像自然閒聊，不像考試。
- 先回應用戶內容，再追問。
- 每輪最多糾正 1-2 個最重要問題。
- 用戶卡住時可以引導：`Do you mean...?`、`You could say...`
- 不要長篇講解。
- 不要每句話都糾錯。
- 用戶中文求助時，用中文簡短解釋，再引導回英文。

## 9. 錄音與存檔

每個 session 建立目錄：

```text
data/recordings/{session_id}/
  full.webm
  user_turn_001.webm
  assistant_turn_001.wav
  transcript.json
  notes.json
  events.jsonl
```

要求：

- 全程錄音必須可回放。
- 每輪 utterance 要能對應音訊片段。
- session 結束後自動保存。
- History 頁面可以查看過往對話、transcript、notes、summary。

## 10. 每日新聞

實現要求：

- 類別：AI科技、時事。
- 默認使用免費 RSS。
- 每天 07:30 自動抓取。
- 每類保留最新 20 篇。
- 去重依據：URL + title hash。
- 不保存全文，只保存摘要、標題、來源、URL。

推薦算法：

```text
score = category_preference + recency_score + interaction_score
```

互動權重：

- 打開文章：+1
- 停留 60 秒以上：+2
- 收藏：+3
- 跳過：-1

新聞卡片展示：

- title
- source
- published date
- summary
- keywords
- difficulty
- open original button

## 11. 詞彙圖片搜索

默認使用 Wikimedia Commons API。

流程：

1. 用戶輸入英文詞。
2. 後端查本地 cache。
3. 若 cache 未命中，請求 Wikimedia。
4. 返回 6-12 張圖片。
5. 保存 30 天 cache。

返回格式：

```json
{
  "word": "apple",
  "results": [
    {
      "image_url": "...",
      "thumbnail_url": "...",
      "source": "Wikimedia Commons",
      "license": "...",
      "description": "..."
    }
  ]
}
```

多義詞處理：

- 第一版不做複雜語義搜索。
- 若結果混雜，前端允許用戶改搜更具體詞，例如 `apple fruit`、`Apple logo`。

## 12. 日記功能

流程：

1. 用戶輸入英文日記。
2. 後端保存原文。
3. 調用 LLM API 分析。
4. 返回結構化建議。
5. 保存修訂結果。

LLM 返回：

```json
{
  "grammar_issues": [
    {
      "original": "...",
      "corrected": "...",
      "explanation_zh": "..."
    }
  ],
  "better_version": "...",
  "sentence_upgrades": [
    {
      "original": "...",
      "upgraded": "..."
    }
  ],
  "useful_phrases": ["...", "..."],
  "overall_advice": "..."
}
```

要求：

- 保持原意。
- 不要改得過度高級。
- 解釋用中文，例句用英文。
- History 可按日期查看。

## 13. 故事復述遊戲

流程：

1. 用戶進入 Retell Game。
2. 系統根據當前 level 選擇故事。
3. 前端只播放音訊，不顯示文本。
4. 同時彈出 note 面板。
5. 故事播放完，倒計時 30 秒。
6. 用戶開始 retell。
7. 錄音結束後 ASR 轉寫。
8. LLM 將用戶 transcript 與原故事比對。
9. 返回分數與建議。
10. 達標解鎖下一關。

Level 設計：

- Level 1：45-60 秒，單線故事，少量人物。
- Level 2：60-90 秒，有 3-4 個事件。
- Level 3：90-120 秒，多事件，有轉折。
- Level 4：多人物、多時間線。
- Level 5：有寓意、推理、抽象主旨。

評分：

```text
main_idea: 35
key_events: 35
sequence_logic: 15
language_clarity: 15
```

通關條件：

```text
total_score >= 75
main_idea >= 20
```

返回格式：

```json
{
  "total_score": 82,
  "passed": true,
  "scores": {
    "main_idea": 30,
    "key_events": 28,
    "sequence_logic": 12,
    "language_clarity": 12
  },
  "missed_points": ["...", "..."],
  "language_feedback": ["...", "..."],
  "next_tip": "..."
}
```

## 14. 成本控制

月預算：50 RMB。

策略：

- 默認所有語音、ASR、TTS、本地存儲都不產生 API 成本。
- LLM API 調用走用戶配置的 base URL 和 key。
- 系統必須統計每次 LLM 調用的估算成本。
- Settings 中允許配置每 1K input/output token 價格。
- 如果 provider 不返回 usage，按字符估算 token。
- 月累計達到 45 RMB：前端顯示警告。
- 月累計達到 50 RMB：阻止非必要 AI 調用。
- Speaking 主流程在超預算時可以只保存錄音和 transcript，不再生成 AI 回覆。

成本估算字段：

```text
input_price_per_1k_tokens_rmb
output_price_per_1k_tokens_rmb
monthly_budget_rmb
monthly_used_rmb
```

## 15. UI 頁面

### Dashboard

展示：

- 今日練習時長
- 本月成本
- 最近 session
- 今日新聞
- 故事關卡進度

### Speaking Room

布局：

- 左側：對話區
- 右側：notes + corrections
- 底部：錄音按鈕、暫停、結束、音量波形
- 頂部：topic、focus、session timer

### News Page

- 分類 tab：AI科技、時事
- 新聞卡片列表
- 收藏、跳過、打開原文

### Vocab Image Page

- 搜索框
- 圖片 grid
- 來源和 license

### Diary Page

- 日記輸入區
- AI feedback 區
- 修訂版
- 歷史列表

### Retell Game Page

- level map
- 播放故事
- note window
- countdown
- retell recording
- score result

### Settings Page

- LLM base URL
- API key
- model
- token price
- monthly budget
- ASR model
- TTS voice
- test connection button

## 16. 測試要求

後端 pytest：

- LLM client 可正確讀取 base URL、API key、model。
- budget 超過 50 RMB 時阻止調用。
- Speaking session 可建立、結束、保存。
- Diary 返回結構化 JSON。
- Story scoring 通關邏輯正確。
- Vocab cache 正常命中。
- News 去重正常。

前端 Playwright：

- Settings 可保存 LLM 配置。
- Speaking 可開始錄音、2 秒靜音觸發 turn end。
- Diary 可提交並顯示建議。
- Vocab 可展示圖片。
- Story 未通關不能解鎖下一關。
- History 可查看錄音和 transcript。

音訊測試：

- 提供 3 個 fixtures：
  - 正常英文回答
  - 有長停頓
  - 背景噪音
- 驗證 VAD、ASR、錄音保存。

## 17. 驗收標準

- 不配置 OpenAI 或付費 API 時，App 可啟動。
- 配置 OpenAI-compatible LLM 後，日記、口語回覆、故事評分可用。
- 每次口語練習能保存完整錄音。
- 2 秒靜音能自動觸發回覆。
- AI 糾錯不超過每輪 1-2 條。
- 月成本超過 50 RMB 時系統阻止繼續付費調用。
- 每日新聞可免費抓取並分類。
- 詞彙圖片搜索能返回圖片和來源。
- 故事復述有關卡、評分、解鎖機制。
- 所有資料默認保存在本地 `data/` 目錄。

## 18. 開發排期

### 第 1 週

- 建立 FastAPI + React 項目。
- 完成 SQLite、資料模型、Settings、LLM client。
- 完成 budget service。

### 第 2 週

- 完成錄音前端。
- 完成 WebSocket。
- 完成 VAD。
- 完成 faster-whisper ASR。

### 第 3 週

- 完成口語 LLM prompt。
- 完成 Piper TTS。
- 完成 Speaking Room。
- 完成錄音存檔。

### 第 4 週

- 完成 Diary。
- 完成 Vocab Image。
- 完成 News RSS。

### 第 5 週

- 完成 Retell Game。
- 完成 story generation、TTS、scoring。
- 完成 level unlock。

### 第 6 週

- 完成 History。
- 完成錯誤處理。
- 完成測試。
- 完成 README 和本地啟動腳本。

## 19. Agent 任務拆分

- Agent A：後端架構、DB、Settings、Budget、LLM client。
- Agent B：Speaking pipeline、WebSocket、VAD、ASR、TTS、錄音。
- Agent C：React UI、Dashboard、Speaking Room、History、Settings。
- Agent D：Diary、News、Vocab Image。
- Agent E：Story Retell Game、關卡、評分、解鎖。
- Agent F：測試、README、安裝腳本、端到端驗收。

## 20. 明確假設

- 第一版是本地個人 Web App，不做商業 SaaS。
- LLM 只通過用戶提供的 OpenAI-compatible API 調用。
- 本地不直接運行大型 LLM。
- 口語聲音用本地 Piper 英式 voice，成本為 0，但不能保證完全真人級。
- 50 RMB/月預算內，不能把雲端 realtime voice 作為日常口語主方案。
- 所有錄音、日記、notes 默認保存在本機。