# Online Audio Capture

A lightweight, browser-based web application for real-time audio capture during Zoom meetings. Participants join via unique email links and record their microphone audio directly in the browser using WebRTC, while simultaneously participating in a Zoom call.

## Architecture

```
┌─────────────────────┐       WebSocket (PCM chunks)       ┌──────────────────┐
│  Participant Browser │ ──────────────────────────────────▶ │  FastAPI Server  │
│                      │                                     │                  │
│  getUserMedia API    │       ACK / heartbeat              │  Assembles WAV   │
│  + AudioContext      │ ◀────────────────────────────────── │  files on disk   │
│  + ScriptProcessor   │                                     │                  │
└─────────────────────┘                                     └──────────────────┘
         ▲                                                          │
         │  Email invite (via Resend)                               ▼
         └─────────────────────────────────────────────      uploads/*.wav
```

## Features

- **Real-time audio streaming** — PCM chunks uploaded every 2 seconds via WebSocket
- **Email invites** — Send styled invite emails with one-click recording links via Resend
- **Single-use, time-limited tokens** — Each link expires after 24 hours and can only be used once
- **Admin dashboard** — Create sessions, monitor active recordings, track participant status, download recordings
- **Participant recording page** — Minimal UI with audio level visualization, status indicators, and a "Stop & Submit" flow
- **Recording management** — Recordings are associated with participant email/name and downloadable from the admin panel

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the server

```bash
python server.py
```

Server runs at **http://localhost:8000**

### 3. Create a session

Open http://localhost:8000 in your browser. You can either:

- **Quick Invite** — Enter a single email address and send an invite instantly
- **Batch Session** — Enter multiple participants (one per line) and optionally send emails to all of them

### 4. Participants record

When a participant clicks their link:

1. The page requests microphone permission
2. They click **Start Recording**
3. Audio streams to the server in real time via WebSocket
4. They keep the tab open while on their Zoom call
5. They click **Stop & Submit** when done — a confirmation screen appears

### 5. Download recordings

All recordings appear in the **Saved Recordings** table on the admin dashboard with participant name, session, duration, and a **Download** link. Files are also available on disk in the `uploads/` directory.

---

## Email Setup (Resend)

The app uses [Resend](https://resend.com) to send invite emails. Email is optional — if not configured, you can still generate links and share them manually.

### Step 1 — Create a Resend account

Go to [https://resend.com/signup](https://resend.com/signup) and create a free account. The free tier includes 100 emails per day.

### Step 2 — Get your API key

1. Sign in to [Resend](https://resend.com)
2. Go to **API Keys** in the left sidebar
3. Click **Create API Key**
4. Copy the key (it starts with `re_`)

### Step 3 — Set the environment variable

```bash
export RESEND_API_KEY="re_your_actual_key_here"
```

Then start the server:

```bash
python server.py
```

You should see `RESEND_API_KEY: configured` in the startup output.

### Step 4 — Verify your own domain (required for sending to others)

> **Important:** On the free tier with the default sender (`onboarding@resend.dev`), Resend **only delivers emails to the address you signed up with**. To send to any recipient (including institutional emails like `.edu`), you must verify your own domain.

1. Go to [https://resend.com/domains](https://resend.com/domains)
2. Click **Add Domain** and enter a domain you own (e.g., `yourdomain.com`)
3. Resend will provide DNS records to add — typically:
   - **SPF** record (TXT)
   - **DKIM** records (TXT or CNAME)
   - **DMARC** record (TXT, optional but recommended)
4. Add these records in your domain's DNS settings (Namecheap, Cloudflare, Google Domains, etc.)
5. Wait for verification — usually a few minutes, sometimes up to a few hours
6. Once verified (green status in Resend), update your from address:

```bash
export RESEND_FROM_EMAIL="Audio Capture <invite@yourdomain.com>"
export RESEND_API_KEY="re_your_actual_key_here"
python server.py
```

### If you don't own a domain

You have two options:

- **Buy a cheap domain** — $1–12/year from providers like Namecheap or Cloudflare Registrar, then verify it in Resend
- **Skip email entirely** — Generate links from the admin dashboard and share them manually via Slack, iMessage, or your own email client. The recording links work regardless of how the participant receives them.

### Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `RESEND_API_KEY` | _(empty)_ | Your Resend API key. If not set, email sending is disabled. |
| `RESEND_FROM_EMAIL` | `Audio Capture <onboarding@resend.dev>` | The "from" address for invite emails. Change to your verified domain. |
| `APP_BASE_URL` | `http://localhost:8000` | Base URL for links in emails. Set to your public domain in production. |

### Example: full setup

```bash
export RESEND_API_KEY="re_abc123xyz"
export RESEND_FROM_EMAIL="Recording Invites <invites@mycompany.com>"
export APP_BASE_URL="https://capture.mycompany.com"
python server.py
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Admin dashboard |
| `GET` | `/record/{token}` | Participant recording page |
| `POST` | `/api/sessions` | Create session + generate links (optional email) |
| `POST` | `/api/invite` | Quick single-participant invite with email |
| `GET` | `/api/tokens` | List all tokens with status |
| `GET` | `/api/recordings` | List saved recordings with participant info |
| `GET` | `/api/recordings/{filename}/download` | Download a recording file |
| `GET` | `/api/active` | List active recording sessions |
| `WS` | `/ws/audio/{token}` | WebSocket for audio streaming |

### Create Session Example

```bash
curl -X POST http://localhost:8000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "session_name": "Weekly Standup",
    "participants": [
      {"email": "alice@example.com", "name": "Alice"},
      {"email": "bob@example.com", "name": "Bob"}
    ],
    "send_emails": true
  }'
```

### Quick Invite Example

```bash
curl -X POST http://localhost:8000/api/invite \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice@example.com",
    "name": "Alice",
    "session_name": "Interview Recording"
  }'
```

---

## Technical Details

- **Audio format**: WAV (48kHz, 16-bit PCM, mono)
- **Chunk interval**: Every 2 seconds, raw PCM data is sent via WebSocket
- **Token format**: 16-character hex UUID, single-use, expires after 24 hours
- **Token status lifecycle**: `pending` → `recording` → `completed` (or `expired`)
- **Reconnection**: Auto-reconnects up to 5 times on WebSocket disconnect
- **Storage**: Single consolidated WAV file per participant per session, stored in `uploads/`
- **Token storage**: JSON files in `tokens/`, one per invite
- **Browser APIs**: `getUserMedia`, `AudioContext`, `ScriptProcessorNode`, `WebSocket`

## Project Structure

```
online-audio-capture/
├── server.py              # FastAPI backend
├── requirements.txt       # Python dependencies
├── templates/
│   ├── index.html         # Admin dashboard
│   ├── record.html        # Participant recording page
│   └── error.html         # Error page (expired/invalid links)
├── uploads/               # Saved WAV recordings (auto-created)
├── tokens/                # Token JSON files (auto-created)
└── static/                # Static assets (auto-created)
```

## Important Notes

- Only captures microphone input — does **not** capture system/desktop audio
- Works alongside Zoom without interference (separate audio streams)
- Participants should keep the recording tab open (not closed) during the Zoom call
- The server must be running for the entire duration of the recording session
- HTTPS is required in production for `getUserMedia` to work (`localhost` is exempt)

---

## License & Copyright

© 2025 UCLA Health. All rights reserved.

This software was developed for UCLA Health data collection purposes. Unauthorized use, reproduction, or distribution of this application or any portion of it is strictly prohibited without prior written consent from UCLA Health.

For questions or permissions, contact UCLA Health.
