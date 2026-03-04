# ParaFast

Multi-agent voice assistant for EMS (Emergency Medical Services) paramedic workers. Para AI helps with shifts, reports, pre-shift checklists, and more via chat and voice.

---

## Architecture

- **Supervisor** — Routes user requests to specialized agents
- **Shift Agent** — Schedule lookup, day-off requests, shift swaps
- **Scribe Agent** — Occurrence reports, Teddy Bear forms, ACRCs
- **Pre-Shift Agent** — Checklist (Form 4), blocking items (CERT-DL, ACRC)

**Flow:** User (chat/voice) → Supervisor → Agent → Tools → Response

---

## Quick Start

```bash
cd backend
pip install -r requirements.txt
# Copy .env.example to .env and fill in values
uvicorn backend.main:app --reload
```

Open http://localhost:8000 for chat, http://localhost:8000/voice for voice.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase anon/service key |
| `JWT_SECRET` | Yes | Secret for JWT signing |
| `OPEN_ROUTER_API_KEY` | Yes | OpenRouter API key (Gemini) |
| `RESEND_API_KEY` | Yes | Resend email API key |
| `TARGET_DISPATCH_EMAIL` | No | Default: `yakshpatel4826@gmail.com` |
| `RESEND_FROM_EMAIL` | No | Verified domain sender (e.g. `ParaFast <reports@domain.com>`) |
| `DEEPGRAM_API_KEY` | Yes (voice) | Deepgram STT |
| `ELEVENLABS_API_KEY` | Yes (voice) | ElevenLabs TTS |
| `ELEVENLABS_VOICE_ID` | No | Default voice ID |
| `VOICE_ID_MALE` | No | Voice for male users (opposite-gender demo) |
| `VOICE_ID_FEMALE` | No | Voice for female users |
| `SHIFT_SCHEDULE_URL` | No | URL to scrape shift calendar |
| `SHIFT_REQUEST_FORM_URL` | No | Shift change form URL |

---

## API Endpoints

### Public (no auth)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Chat UI (HTML) |
| `GET` | `/voice` | Voice UI (HTML) |
| `GET` | `/test-email` | Send test email (checks Resend config) |
| `POST` | `/auth/signup` | Register new user |
| `POST` | `/auth/login` | Log in |
| `POST` | `/auth/logout` | Log out (clears cookie) |

### Protected (cookie or Bearer token)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/auth/me` | Current user profile |
| `POST` | `/chat` | Send message to Para AI |
| `POST` | `/update_draft` | Patch form draft |
| `POST` | `/submit_and_email` | Submit draft and email report |

### WebSocket

| Path | Auth | Description |
|------|------|-------------|
| `WS /ws/voice` | Cookie or `?token=JWT` | Voice session (STT → Agent → TTS) |

---

## Endpoint Details

### `POST /auth/signup`

**Request:**
```json
{
  "badge_number": "B001",
  "first_name": "John",
  "last_name": "Doe",
  "team_number": "Team01",
  "phone_number": "555-1234",
  "password": "secret"
}
```

**Response:** `access_token`, `badge_number`, `name` + cookie set

---

### `POST /auth/login`

**Request:**
```json
{
  "badge_number": "B001",
  "password": "secret"
}
```

**Response:** `access_token`, `badge_number`, `name` + cookie set

---

### `GET /auth/me`

**Headers:** `Cookie: access_token=...` or `Authorization: Bearer <token>`

**Response:**
```json
{
  "id": "uuid",
  "badge_number": "B001",
  "first_name": "John",
  "last_name": "Doe",
  "team_number": "Team01",
  "phone_number": "555-1234",
  "role": null
}
```

---

### `POST /chat`

**Request:**
```json
{
  "message": "When do I work next week?"
}
```

**Response:**
```json
{
  "reply": "Yeah, you're on Monday next week — 7 to 7 at Station 5, Unit 1122."
}
```

---

### `POST /update_draft`

**Request:**
```json
{
  "patch": {
    "occurrence_type": "MVA",
    "brief_description": "Two-car collision"
  }
}
```

**Response:**
```json
{
  "badge_number": "B001",
  "status": "draft",
  "content": { ... }
}
```

---

### `POST /submit_and_email`

Submits current draft and emails to `TARGET_DISPATCH_EMAIL`.

**Response:**
```json
{
  "message": "Draft submitted and emailed.",
  "status": "submitted"
}
```

---

### `GET /test-email`

Sends a test email to `TARGET_DISPATCH_EMAIL`. No auth.

**Response:**
```json
{
  "ok": true,
  "to": "user@example.com",
  "id": "resend-email-id",
  "message": "Check your inbox (and spam folder)"
}
```

---

## Voice WebSocket (`/ws/voice`)

**Connect:** `ws://localhost:8000/ws/voice` (or `?token=JWT` if no cookie)

**Client → Server:**

| Type | Payload | Description |
|------|---------|-------------|
| `handshake` | `{ "token": "JWT" }` | Auth (if no cookie) |
| `end_utterance` | `{ "data": "base64_audio" }` | Recorded audio (WebM) |

**Server → Client:**

| Type | Payload | Description |
|------|---------|-------------|
| `transcript` | `{ "text": "..." }` | User speech |
| `transcript` | `{ "text": "", "reply": "I didn't catch that..." }` | No speech |
| `loading` | `{ "message": "Thinking..." }` | Processing |
| `reply` | `{ "text": "..." }` | Agent reply |
| `audio` | `{ "data": "base64", "format": "mp3" }` | TTS audio |
| `error` | `{ "detail": "..." }` | Error message |

---

## Database (Supabase)

Run migrations in `backend/supabase_migrations/`:

- `001_form_drafts.sql` — Draft storage
- `002_preshift_checks.sql` — Pre-shift checklist
- `003_voice_preference.sql` — Voice ID per user

---

## Project Structure

```
ParaFast/
├── backend/
│   ├── main.py              # FastAPI app, routes
│   ├── auth.py              # JWT, cookies
│   ├── database.py          # Supabase client
│   ├── schemas.py           # Pydantic models
│   ├── chat.html
│   ├── voice.html
│   ├── app/
│   │   ├── agents/
│   │   │   ├── supervisor.py
│   │   │   ├── shift_agent.py
│   │   │   ├── scribe_agent.py
│   │   │   └── preshift_agent.py
│   │   │   └── tools/
│   │   └── voice/
│   │       └── handler.py    # WebSocket, Deepgram, ElevenLabs
│   └── supabase_migrations/
└── README.md
```

---

## License

Proprietary — EAI Ambulance Service
