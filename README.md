# Zoom Audio Capture

A lightweight, browser-based web application for real-time audio capture during Zoom meetings. Participants join via unique email links and record their microphone audio directly in the browser using WebRTC, while simultaneously participating in a Zoom call.

## Architecture

```
┌─────────────────────┐       WebSocket (PCM chunks)       ┌──────────────────┐
│  Participant Browser │ ──────────────────────────────────▶ │  FastAPI Server  │
│                      │                                     │                  │
│  MediaRecorder API   │       ACK / heartbeat              │  Assembles WAV   │
│  + AudioContext      │ ◀────────────────────────────────── │  files on disk   │
│  + ScriptProcessor   │                                     │                  │
└─────────────────────┘                                     └──────────────────┘
                                                                    │
                                                                    ▼
                                                             uploads/*.wav
```

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

### 3. Create a session (Admin)

Open http://localhost:8000 in your browser. Enter a session name and participant emails, then click **Generate Links**. Each participant gets a unique URL.

### 4. Share links

Send each participant their unique link. When they open it:

1. The page requests microphone permission
2. They click **Start Recording**
3. Audio streams to the server in real time via WebSocket
4. They keep the tab open while on their Zoom call

### 5. Recordings

All recordings are saved as `.wav` files in the `uploads/` directory. Each file is named: `{session}_{participant}_{timestamp}.wav`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Admin dashboard |
| `GET` | `/record/{token}` | Participant recording page |
| `POST` | `/api/sessions` | Create session + generate links |
| `GET` | `/api/tokens` | List all tokens |
| `GET` | `/api/recordings` | List saved recordings |
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
    ]
  }'
```

## Technical Details

- **Audio format**: WAV (48kHz, 16-bit, mono)
- **Chunk interval**: Every 2 seconds, raw PCM data is sent via WebSocket
- **Token format**: 16-char hex UUID, single-use
- **Reconnection**: Auto-reconnects up to 5 times on WebSocket disconnect
- **Storage**: Single consolidated WAV file per participant per session
- **Browser APIs**: `getUserMedia`, `AudioContext`, `ScriptProcessorNode`, `WebSocket`

## Notes

- Only captures microphone input — does **not** capture system/desktop audio
- Works alongside Zoom without interference (separate audio streams)
- Participants should keep the recording tab open (not closed) during the Zoom call
- HTTPS is required in production for `getUserMedia` to work (localhost is exempt)
