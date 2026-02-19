"""
Zoom Audio Capture — FastAPI Backend
Real-time chunked audio upload with token-based participant tracking,
email invites via Resend, token expiry, and recording management.
"""

import os
import uuid
import json
import wave
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
TOKEN_DIR = BASE_DIR / "tokens"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

for d in [UPLOAD_DIR, TOKEN_DIR, TEMPLATES_DIR, STATIC_DIR]:
    d.mkdir(exist_ok=True)

# Resend API key — set via environment variable
# export RESEND_API_KEY="re_xxxxxxxxxxxx"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "Audio Capture <onboarding@resend.dev>")

# Base URL for links in emails — set to your public domain in production
# export APP_BASE_URL="https://yourdomain.com"
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

TOKEN_EXPIRY_HOURS = 24
SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit

print(f"\n{'='*50}")
print(f"  BASE_DIR:      {BASE_DIR}")
print(f"  TEMPLATES_DIR: {TEMPLATES_DIR}")
print(f"  Templates:     {[f.name for f in TEMPLATES_DIR.glob('*.html')]}")
print(f"  RESEND_API_KEY: {'configured' if RESEND_API_KEY else 'not set (emails disabled)'}")
print(f"  APP_BASE_URL:  {APP_BASE_URL}")
print(f"{'='*50}\n")

app = FastAPI(title="Zoom Audio Capture")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    send_emails: bool = False


class QuickInvite(BaseModel):
    email: str
    name: Optional[str] = None
    session_name: Optional[str] = "Recording Session"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------
def _token_path(token: str) -> Path:
    return TOKEN_DIR / f"{token}.json"


def create_token(email: str, name: str | None, session_name: str) -> str:
    token = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc)
    data = {
        "token": token,
        "email": email,
        "name": name or email.split("@")[0],
        "session_name": session_name,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=TOKEN_EXPIRY_HOURS)).isoformat(),
        "status": "pending",        # pending -> recording -> completed -> expired
        "recording_file": None,
        "recording_size": None,
        "recording_duration_sec": None,
        "email_sent": False,
    }
    _token_path(token).write_text(json.dumps(data, indent=2))
    return token


def validate_token(token: str) -> dict | None:
    path = _token_path(token)
    if not path.exists():
        return None
    data = json.loads(path.read_text())

    # Check expiry
    expires = datetime.fromisoformat(data["expires_at"])
    if datetime.now(timezone.utc) > expires and data["status"] == "pending":
        data["status"] = "expired"
        path.write_text(json.dumps(data, indent=2))
        return None

    # Check if already completed (single-use)
    if data["status"] == "completed":
        return None

    return data


def update_token(token: str, updates: dict):
    path = _token_path(token)
    if path.exists():
        data = json.loads(path.read_text())
        data.update(updates)
        path.write_text(json.dumps(data, indent=2))


def get_token_raw(token: str) -> dict | None:
    """Get token data without validation (for admin views)."""
    path = _token_path(token)
    if not path.exists():
        return None
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Email helpers (Resend)
# ---------------------------------------------------------------------------
async def send_invite_email(email: str, name: str, session_name: str, token: str) -> bool:
    """Send an invite email via Resend API. Returns True on success."""
    if not RESEND_API_KEY:
        print(f"  [email skipped - no RESEND_API_KEY] {email}")
        return False

    record_url = f"{APP_BASE_URL}/record/{token}"
    display_name = name or email.split("@")[0]

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 520px; margin: 0 auto; padding: 40px 20px;">
        <div style="background: #12151c; border: 1px solid #242938; border-radius: 16px; padding: 36px 32px; color: #e2e4ea;">
            <div style="text-align: center; margin-bottom: 28px;">
                <div style="display: inline-block; width: 44px; height: 44px; background: #22d67a; border-radius: 12px; line-height: 44px; font-size: 22px;">&#127908;</div>
            </div>
            <h1 style="font-size: 20px; font-weight: 700; text-align: center; margin: 0 0 8px; color: #e2e4ea;">
                You're Invited to Record
            </h1>
            <p style="font-size: 14px; color: #7a7f92; text-align: center; margin: 0 0 28px; line-height: 1.6;">
                Hi {display_name}, you've been invited to join<br>
                <strong style="color: #e2e4ea;">{session_name}</strong>
            </p>
            <div style="text-align: center; margin-bottom: 28px;">
                <a href="{record_url}"
                   style="display: inline-block; background: #22d67a; color: #0b0d11; padding: 14px 36px;
                          border-radius: 10px; font-weight: 700; font-size: 15px; text-decoration: none;">
                    Open Recording Page
                </a>
            </div>
            <div style="border-top: 1px solid #242938; padding-top: 20px; font-size: 12px; color: #7a7f92; line-height: 1.6;">
                <p><strong style="color: #e2e4ea;">How it works:</strong></p>
                <p>1. Click the button above to open the recording page</p>
                <p>2. Allow microphone access and click Start Recording</p>
                <p>3. Keep the tab open while you're on your Zoom call</p>
                <p>4. Click Stop &amp; Submit when you're done</p>
                <p style="margin-top: 16px; color: #f0a030;">This link expires in {TOKEN_EXPIRY_HOURS} hours and can only be used once.</p>
            </div>
        </div>
    </div>
    """

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": RESEND_FROM_EMAIL,
                    "to": [email],
                    "subject": f"Recording Invite: {session_name}",
                    "html": html_body,
                },
                timeout=10,
            )
            if resp.status_code in (200, 201):
                print(f"  [email sent] {email}")
                return True
            else:
                print(f"  [email failed] {email}: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        print(f"  [email error] {email}: {e}")
        return False


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
        raw = get_token_raw(token)
        if raw and raw.get("status") == "expired":
            msg = "This recording link has expired. Please request a new one from the session organizer."
        elif raw and raw.get("status") == "completed":
            msg = "This recording link has already been used. Each link can only be used once."
        else:
            msg = "Invalid or expired link. Please check with the session organizer for a new invite."
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": msg},
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
        link = f"/record/{token}"
        full_link = f"{APP_BASE_URL}{link}"

        email_sent = False
        if payload.send_emails:
            email_sent = await send_invite_email(
                p.email, p.name, payload.session_name, token
            )
            update_token(token, {"email_sent": email_sent})

        results.append(
            {
                "email": p.email,
                "name": p.name,
                "token": token,
                "link": link,
                "full_link": full_link,
                "email_sent": email_sent,
            }
        )
    return {"session_name": payload.session_name, "participants": results}


@app.post("/api/invite")
async def quick_invite(payload: QuickInvite):
    """Quick single-participant invite with email."""
    token = create_token(payload.email, payload.name, payload.session_name)
    link = f"/record/{token}"
    full_link = f"{APP_BASE_URL}{link}"

    email_sent = await send_invite_email(
        payload.email, payload.name, payload.session_name, token
    )
    update_token(token, {"email_sent": email_sent})

    return {
        "email": payload.email,
        "name": payload.name or payload.email.split("@")[0],
        "token": token,
        "link": link,
        "full_link": full_link,
        "email_sent": email_sent,
        "email_configured": bool(RESEND_API_KEY),
    }


@app.get("/api/tokens")
async def list_tokens():
    """List all generated tokens (admin view)."""
    tokens = []
    for f in TOKEN_DIR.glob("*.json"):
        data = json.loads(f.read_text())
        if data.get("status") == "pending":
            expires = datetime.fromisoformat(data["expires_at"])
            if datetime.now(timezone.utc) > expires:
                data["status"] = "expired"
                f.write_text(json.dumps(data, indent=2))
        tokens.append(data)
    tokens.sort(key=lambda t: t["created_at"], reverse=True)
    return {"tokens": tokens}


@app.get("/api/recordings")
async def list_recordings():
    """List all saved recordings with associated participant info."""
    recordings = []
    # Build a lookup from filename -> token data
    token_lookup = {}
    for tf in TOKEN_DIR.glob("*.json"):
        td = json.loads(tf.read_text())
        if td.get("recording_file"):
            token_lookup[td["recording_file"]] = td

    for f in UPLOAD_DIR.glob("*.wav"):
        stat = f.stat()
        ti = token_lookup.get(f.name)
        recordings.append(
            {
                "filename": f.name,
                "size_bytes": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "participant_name": ti["name"] if ti else "Unknown",
                "participant_email": ti["email"] if ti else "",
                "session_name": ti["session_name"] if ti else "",
                "duration_sec": ti.get("recording_duration_sec") if ti else None,
            }
        )
    recordings.sort(key=lambda r: r["created"], reverse=True)
    return {"recordings": recordings}


@app.get("/api/recordings/{filename}/download")
async def download_recording(filename: str):
    """Download a recording file."""
    filepath = UPLOAD_DIR / filename
    if not filepath.exists() or not filepath.suffix == ".wav":
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="audio/wav",
    )


# ---------------------------------------------------------------------------
# WebSocket — real-time audio upload
# ---------------------------------------------------------------------------
@app.websocket("/ws/audio/{token}")
async def audio_websocket(websocket: WebSocket, token: str):
    info = validate_token(token)
    if info is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    if info.get("status") == "completed":
        await websocket.close(code=4002, reason="Token already used")
        return

    await websocket.accept()
    update_token(token, {"status": "recording"})

    safe_name = info["name"].replace(" ", "_")[:30]
    safe_session = info["session_name"].replace(" ", "_")[:30]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_session}_{safe_name}_{timestamp}.wav"
    filepath = UPLOAD_DIR / filename

    wf = init_wav_file(filepath)
    chunk_count = 0
    start_time = datetime.now(timezone.utc)

    active_sessions[token] = {
        "token": token,
        "name": info["name"],
        "email": info["email"],
        "session_name": info["session_name"],
        "file": filename,
        "started": start_time.isoformat(),
        "chunks": 0,
    }

    try:
        while True:
            data = await websocket.receive_bytes()
            wf.writeframes(data)
            chunk_count += 1
            active_sessions[token]["chunks"] = chunk_count

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
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        file_size = filepath.stat().st_size if filepath.exists() else 0

        update_token(token, {
            "status": "completed",
            "recording_file": filename,
            "recording_size": file_size,
            "recording_duration_sec": round(elapsed, 1),
        })
        print(f"Saved {filepath.name} ({chunk_count} chunks, {elapsed:.0f}s, {file_size/1024:.0f}KB)")


@app.get("/api/active")
async def active_connections():
    return {"active": list(active_sessions.values())}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
