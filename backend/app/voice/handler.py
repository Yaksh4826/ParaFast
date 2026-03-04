"""Voice Layer - WebSocket handler for Para AI.

Flow: Audio (Deepgram STT) -> Agent (LangGraph) -> TTS (ElevenLabs) -> Client
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.auth import decode_token
    from backend.app.agents.supervisor import run_supervisor
except ModuleNotFoundError:
    from auth import decode_token  # type: ignore
    from app.agents.supervisor import run_supervisor  # type: ignore

logger = logging.getLogger(__name__)

DEEPGRAM_API_KEY = (os.getenv("DEEPGRAM_API_KEY") or "").strip()
ELEVENLABS_API_KEY = (os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_LABS_API_KEY") or "").strip()
ELEVENLABS_VOICE_ID = (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb").strip()
VOICE_ID_MALE = (os.getenv("VOICE_ID_MALE") or ELEVENLABS_VOICE_ID).strip()
VOICE_ID_FEMALE = (os.getenv("VOICE_ID_FEMALE") or "EXAVITQu4vr4xnSDxMaL").strip()


def _get_user_voice_id(badge_number: str) -> str:
    """Per-user voice: profile.voice_id overrides. Demo: opposite gender if no preference."""
    try:
        from backend.database import get_supabase_client
    except ModuleNotFoundError:
        from database import get_supabase_client  # type: ignore
    try:
        sb = get_supabase_client()
        resp = sb.table("profiles").select("voice_id, gender").eq("badge_number", badge_number).limit(1).execute()
        if resp.data:
            row = resp.data[0]
            vid = (row.get("voice_id") or "").strip()
            if vid:
                return vid
            gender = (row.get("gender") or "").strip().upper()
            if gender == "M" or gender == "MALE":
                return VOICE_ID_FEMALE
            if gender == "F" or gender == "FEMALE":
                return VOICE_ID_MALE
    except Exception:
        pass
    return ELEVENLABS_VOICE_ID


async def _stream_elevenlabs(text: str, voice_id: str) -> bytes:
    """TTS from ElevenLabs. Uses convert endpoint (more reliable than stream)."""
    if not ELEVENLABS_API_KEY:
        raise ValueError("ELEVENLABS_API_KEY not set")
    import httpx
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.content


async def _transcribe_deepgram(audio_bytes: bytes) -> str:
    """Transcribe audio via Deepgram REST (simpler than WebSocket for chunked input)."""
    if not DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY not set")
    import httpx
    url = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "audio/webm",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, content=audio_bytes, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or {}
    channels = results.get("channels") or [{}]
    channel = channels[0] if channels else {}
    alternatives = channel.get("alternatives") or [{}]
    alt = alternatives[0] if alternatives else {}
    return (alt.get("transcript", "") or "").strip()


async def handle_voice_websocket(websocket, badge_number: str, history: list):
    """Handle a single voice session. Accepts end_utterance with base64 audio in one message."""
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("type") != "websocket.receive":
                continue
            if "text" not in msg:
                continue
            try:
                data = json.loads(msg["text"])
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type", "")
            if msg_type == "end_utterance":
                chunk_b64 = data.get("data", "")
                audio_buffer = base64.b64decode(chunk_b64) if chunk_b64 else b""
                buf_len = len(audio_buffer)
                if buf_len < 500:
                    logger.warning("Voice: audio buffer too small (%d bytes)", buf_len)
                    await websocket.send_json({
                        "type": "error",
                        "detail": "No speech detected. Speak for at least a second, then click Stop.",
                    })
                    continue
                try:
                    text = await _transcribe_deepgram(audio_buffer)
                except Exception as e:
                    logger.exception("Deepgram error")
                    await websocket.send_json({"type": "error", "detail": str(e)})
                    continue
                if not text:
                    await websocket.send_json({
                        "type": "transcript",
                        "text": "",
                        "reply": "I didn't catch that. Could you repeat?",
                    })
                    continue
                await websocket.send_json({"type": "transcript", "text": text})
                await websocket.send_json({"type": "loading", "message": "Thinking..."})
                try:
                    reply, new_history = await run_supervisor(text, badge_number, history)
                    history.clear()
                    history.extend(new_history)
                except Exception as e:
                    logger.exception("Agent error")
                    await websocket.send_json({"type": "error", "detail": str(e)})
                    continue
                await websocket.send_json({"type": "reply", "text": reply})
                voice_id = _get_user_voice_id(badge_number)
                try:
                    audio_bytes = await _stream_elevenlabs(reply, voice_id)
                    await websocket.send_json({
                        "type": "audio",
                        "data": base64.b64encode(audio_bytes).decode(),
                        "format": "mp3",
                    })
                except Exception as e:
                    logger.exception("ElevenLabs error")
                    await websocket.send_json({"type": "error", "detail": str(e)})
    except Exception as e:
        logger.exception("Voice WebSocket error: %s", e)
