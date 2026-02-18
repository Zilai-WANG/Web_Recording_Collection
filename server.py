"""
Zoom Audio Capture — FastAPI Backend
Real-time chunked audio upload with token-based participant tracking.
"""

import os
import uuid
import json
import wave
import struct
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
TOKEN_DIR = BASE_DIR / "tokens"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOAD_DIR.mkdir(exist_ok=True)
TOKEN_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Debug: print paths on startup so you can verify
print(f"\n{'='*50}")
print(f"  BASE_DIR:      {BASE_DIR}")
print(f"  TEMPLATES_DIR: {TEMPLATES_DIR}")
print(f"  Templates exist: {list(TEMPLATES_DIR.glob('*.html'))}")
print(f"{'='*50}\n")

SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit

app = FastAPI(title="Zoom Audio Capture")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------------------------------------------------------------------------
# In-memory state (for production, use Redis / DB)
# ---------------------------------------------------------------------------
active_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ParticipantCreate(BaseModel):
    email: str
    name: Optional[str] = None
    session_name: Optional[str] = "Default Session"


class SessionCreate(BaseModel):
    session_name: str
    participants: list[ParticipantCreate]


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------
def _token_path(token: str) -> Path:
    return TOKEN_DIR / f"{token}.json"


def create_token(email: str, name: str | None, session_name: str) -> str:
    token = uuid.uuid4().hex[:16]
    data = {
        "token": token,
        "email": email,
        "name": name or email.split("@")[0],
        "session_name": session_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "used": False,
    }
    _token_path(token).write_text(json.dumps(data, indent=2))
    return token


def validate_token(token: str) -> dict | None:
    path = _token_path(token)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def mark_token_used(token: str):
    path = _token_path(token)
    if path.exists():
        data = json.loads(path.read_text())
        data["used"] = True
        path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------
def init_wav_file(filepath: Path) -> wave.Wave_write:
    wf = wave.open(str(filepath), "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH)
    wf.setframerate(SAMPLE_RATE)
    return wf


# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/record/{token}", response_class=HTMLResponse)
async def record_page(request: Request, token: str):
    info = validate_token(token)
    if info is None:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Invalid or expired link."},
            status_code=404,
        )
    return templates.TemplateResponse(
        "record.html",
        {
            "request": request,
            "token": token,
            "participant_name": info["name"],
            "session_name": info["session_name"],
        },
    )


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------
@app.post("/api/sessions")
async def create_session(payload: SessionCreate):
    """Create a session and generate unique links for each participant."""
    results = []
    for p in payload.participants:
        token = create_token(p.email, p.name, payload.session_name)
        results.append(
            {
                "email": p.email,
                "name": p.name,
                "token": token,
                "link": f"/record/{token}",
            }
        )
    return {"session_name": payload.session_name, "participants": results}


@app.get("/api/tokens")
async def list_tokens():
    """List all generated tokens (admin view)."""
    tokens = []
    for f in TOKEN_DIR.glob("*.json"):
        tokens.append(json.loads(f.read_text()))
    tokens.sort(key=lambda t: t["created_at"], reverse=True)
    return {"tokens": tokens}


@app.get("/api/recordings")
async def list_recordings():
    """List all saved recordings."""
    recordings = []
    for f in UPLOAD_DIR.glob("*.wav"):
        stat = f.stat()
        recordings.append(
            {
                "filename": f.name,
                "size_bytes": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    recordings.sort(key=lambda r: r["created"], reverse=True)
    return {"recordings": recordings}


# ---------------------------------------------------------------------------
# WebSocket — real-time audio upload
# ---------------------------------------------------------------------------
@app.websocket("/ws/audio/{token}")
async def audio_websocket(websocket: WebSocket, token: str):
    info = validate_token(token)
    if info is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    mark_token_used(token)

    # Build filename
    safe_name = info["name"].replace(" ", "_")[:30]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{info['session_name']}_{safe_name}_{timestamp}.wav"
    filepath = UPLOAD_DIR / filename

    wf = init_wav_file(filepath)
    chunk_count = 0

    active_sessions[token] = {
        "name": info["name"],
        "email": info["email"],
        "session_name": info["session_name"],
        "file": str(filepath),
        "started": datetime.now(timezone.utc).isoformat(),
        "chunks": 0,
    }

    try:
        while True:
            data = await websocket.receive_bytes()
            wf.writeframes(data)
            chunk_count += 1
            active_sessions[token]["chunks"] = chunk_count

            # Acknowledge every 10 chunks to keep connection alive
            if chunk_count % 10 == 0:
                await websocket.send_json(
                    {"type": "ack", "chunks": chunk_count}
                )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error for {token}: {e}")
    finally:
        wf.close()
        active_sessions.pop(token, None)
        print(f"✓ Saved {filepath} ({chunk_count} chunks)")


@app.get("/api/active")
async def active_connections():
    return {"active": list(active_sessions.values())}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
