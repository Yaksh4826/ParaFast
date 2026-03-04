import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dicttoxml import dicttoxml
from fastapi import Depends, FastAPI, HTTPException
from fpdf import FPDF
from pydantic import ValidationError
import resend

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

if __package__ is None or __package__ == "":
    import pathlib
    import sys

    sys.path.append(str(pathlib.Path(__file__).resolve().parent))
    from database import get_supabase_client  # type: ignore
    from auth import (  # type: ignore
        COOKIE_MAX_AGE,
        COOKIE_NAME,
        create_access_token,
        decode_token,
        get_current_user,
        get_current_user_optional,
        hash_password,
        verify_password,
    )
    from schemas import (  # type: ignore
        SignupRequest,
        LoginRequest,
        OccurrenceReport,
        UpdateDraftRequest,
        AgentChatRequest,
    )
    from app.agents.supervisor import run_supervisor  # type: ignore
    from app.voice.handler import handle_voice_websocket  # type: ignore
else:
    from .database import get_supabase_client
    from .auth import (
        COOKIE_MAX_AGE,
        COOKIE_NAME,
        create_access_token,
        decode_token,
        get_current_user,
        get_current_user_optional,
        hash_password,
        verify_password,
    )
    from .schemas import (
        SignupRequest,
        LoginRequest,
        OccurrenceReport,
        UpdateDraftRequest,
        AgentChatRequest,
    )
    from .app.agents.supervisor import run_supervisor  # type: ignore
    from .app.voice.handler import handle_voice_websocket  # type: ignore

from collections import defaultdict
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="ParaFast - EMS Multi-Agent System")

_chat_histories: dict[str, list] = defaultdict(list)
_voice_histories: dict[str, list] = defaultdict(list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = logging.getLogger(__name__)
supabase = get_supabase_client()


@app.get("/test-email")
async def test_email():
    """Send a test email to verify Resend is working. Check your inbox (and spam)."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    target = (os.getenv("TARGET_DISPATCH_EMAIL") or "yakshpatel4826@gmail.com").strip()
    from_addr = (os.getenv("RESEND_FROM_EMAIL") or "").strip() or "ParaFast AI <onboarding@resend.dev>"
    if not api_key:
        return {"ok": False, "error": "RESEND_API_KEY not set in .env"}
    try:
        resend.api_key = api_key
        resp = resend.Emails.send({
            "from": from_addr,
            "to": [target],
            "subject": "ParaFast Test Email",
            "html": "<p>If you got this, Resend is working.</p>",
        })
        email_id = getattr(resp, "id", None) or (resp.get("id") if isinstance(resp, dict) else None)
        return {
            "ok": True,
            "to": target,
            "id": email_id,
            "message": "Check your inbox (and spam folder)",
            "hint": "If you don't receive it: (1) Check spam. (2) TARGET_DISPATCH_EMAIL must match your Resend account email when using onboarding@resend.dev. (3) Verify a domain at resend.com/domains and set RESEND_FROM_EMAIL=ParaFast <reports@yourdomain.com> to send to any address.",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "hint": "Verify domain at resend.com/domains. Without it, you can only send to your Resend account email."}


def _get_cookie_from_scope(scope: dict, name: str) -> str:
    """Extract cookie value from ASGI scope headers."""
    for h, v in scope.get("headers", []):
        if h == b"cookie":
            for part in v.decode().split(";"):
                part = part.strip()
                if part.startswith(name + "="):
                    return part.split("=", 1)[1].strip().strip('"')
    return ""


@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket):
    """Voice session: token from cookie, query ?token=, or handshake message."""
    await websocket.accept()
    badge_number = ""
    try:
        token = _get_cookie_from_scope(websocket.scope, COOKIE_NAME)
        if not token:
            query = websocket.scope.get("query_string", b"").decode()
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            token = params.get("token", "")
        if token:
            try:
                payload = decode_token(token)
                badge_number = payload.get("sub", "")
            except Exception:
                pass
        if not badge_number:
            first_msg = await websocket.receive()
            if first_msg.get("type") == "websocket.receive" and "text" in first_msg:
                try:
                    data = json.loads(first_msg["text"])
                    if data.get("type") == "handshake":
                        token = data.get("token", "")
                        if token:
                            payload = decode_token(token)
                            badge_number = payload.get("sub", "")
                except Exception:
                    pass
        if not badge_number:
            await websocket.send_json({"type": "error", "detail": "Unauthorized. Connect with ?token=JWT or send handshake with token."})
            await websocket.close(code=4001)
            return
        history = _voice_histories[badge_number]
        await handle_voice_websocket(websocket, badge_number, history)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Voice WebSocket: %s", exc)
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass


@app.get("/voice")
async def serve_voice_ui():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent
    html_path = base / "voice.html"
    if not html_path.exists():
        html_path = base.parent / "backend" / "voice.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="voice.html not found")
    return FileResponse(str(html_path), media_type="text/html")


@app.get("/")
async def serve_chat_ui():
    import pathlib
    html_path = pathlib.Path(__file__).resolve().parent / "chat.html"
    return FileResponse(str(html_path), media_type="text/html")


# ═══════════════════════════════════════════════════════════════════════════
# AUTH — public (no token required)
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/auth/signup")
async def signup(req: SignupRequest):
    existing = (
        supabase.table("profiles")
        .select("id")
        .eq("badge_number", req.badge_number)
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=409, detail="Badge number already registered.")

    profile_id = str(uuid.uuid4())
    supabase.table("profiles").insert({
        "id": profile_id,
        "badge_number": req.badge_number,
        "first_name": req.first_name,
        "last_name": req.last_name,
        "team_number": req.team_number,
        "phone_number": req.phone_number or "",
        "password_hash": hash_password(req.password),
    }).execute()

    token = create_access_token(req.badge_number, profile_id)
    response = JSONResponse(content={
        "access_token": token,
        "token_type": "bearer",
        "badge_number": req.badge_number,
        "name": f"{req.first_name} {req.last_name}",
    })
    response.set_cookie(key=COOKIE_NAME, value=token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response


@app.post("/auth/login")
async def login(req: LoginRequest):
    resp = (
        supabase.table("profiles")
        .select("id, badge_number, first_name, last_name, password_hash")
        .eq("badge_number", req.badge_number)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        raise HTTPException(status_code=401, detail="Invalid badge number or password.")

    user = rows[0]
    if not verify_password(req.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid badge number or password.")

    token = create_access_token(user["badge_number"], user["id"])
    response = JSONResponse(content={
        "access_token": token,
        "token_type": "bearer",
        "badge_number": user["badge_number"],
        "name": f"{user['first_name']} {user['last_name']}",
    })
    response.set_cookie(key=COOKIE_NAME, value=token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response


@app.post("/auth/logout")
async def logout(user: dict | None = Depends(get_current_user_optional)):
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key=COOKIE_NAME)
    if user:
        _chat_histories.pop(user.get("sub"), None)
    return response


@app.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    badge = user["sub"]
    resp = (
        supabase.table("profiles")
        .select("id, badge_number, first_name, last_name, team_number, phone_number, role")
        .eq("badge_number", badge)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return rows[0]


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def fetch_draft(badge_number: str) -> Optional[Dict[str, Any]]:
    response = (
        supabase.table("form_drafts")
        .select("*")
        .eq("badge_number", badge_number)
        .limit(1)
        .execute()
    )
    data = response.data or []
    return data[0] if data else None


def merge_content(existing: Optional[Dict[str, Any]], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    merged.update(patch)
    return merged


def upsert_draft(badge_number: str, content: Dict[str, Any], status: str) -> None:
    supabase.table("form_drafts").upsert(
        {
            "badge_number": badge_number,
            "content": content,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="badge_number",
    ).execute()


def generate_xml_content(data: Dict[str, Any]) -> bytes:
    return dicttoxml(data or {}, custom_root="occurrence_report", attr_type=False)


def _ascii_safe(s: str) -> str:
    """Replace non-ASCII chars so FPDF latin-1 encoding works."""
    if not s:
        return ""
    return str(s).replace("\u2014", "-").replace("\u2013", "-").encode("ascii", "replace").decode("ascii")


def generate_pdf_content(data: Dict[str, Any]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Occurrence Report", ln=True)
    pdf.ln(4)
    pdf.set_font("Arial", size=12)
    if not data:
        pdf.cell(0, 10, "No content available.", ln=True)
    else:
        for key, value in data.items():
            pdf.multi_cell(0, 8, f"{key}: {_ascii_safe(str(value))}")
            pdf.ln(1)
    return pdf.output(dest="S").encode("latin-1")


def send_email_with_attachments(
    to_email: str, badge_number: str, xml_bytes: bytes, pdf_bytes: bytes
) -> None:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY is not configured.")
    resend.api_key = api_key
    resend.Emails.send({
        "from": "ParaFast AI <onboarding@resend.dev>",
        "to": [to_email],
        "subject": f"Occurrence Report for badge {badge_number}",
        "html": "<p>A new occurrence report has been submitted.</p>",
        "attachments": [
            {"filename": "occurrence_report.xml", "content": base64.b64encode(xml_bytes).decode()},
            {"filename": "occurrence_report.pdf", "content": base64.b64encode(pdf_bytes).decode()},
        ],
    })


# ═══════════════════════════════════════════════════════════════════════════
# PROTECTED — require Bearer token
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/update_draft")
async def update_draft(request: UpdateDraftRequest, user: dict = Depends(get_current_user)):
    badge = user["sub"]
    draft = fetch_draft(badge)
    existing_content = draft.get("content") if draft else {}
    merged_content = merge_content(existing_content, request.patch)
    status = draft.get("status") if draft else "draft"
    upsert_draft(badge, merged_content, status)
    return {"badge_number": badge, "status": status, "content": merged_content}


@app.post("/submit_and_email")
async def submit_and_email(user: dict = Depends(get_current_user)):
    badge = user["sub"]
    draft = fetch_draft(badge)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found for badge.")

    content = draft.get("content") or {}
    try:
        OccurrenceReport(**content)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400, detail=f"Draft is missing required fields: {exc.errors()}"
        ) from exc

    xml_bytes = generate_xml_content(content)
    pdf_bytes = generate_pdf_content(content)

    target_email = os.getenv("TARGET_DISPATCH_EMAIL")
    if not target_email:
        raise HTTPException(status_code=500, detail="TARGET_DISPATCH_EMAIL is not configured.")

    try:
        send_email_with_attachments(target_email, badge, xml_bytes, pdf_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Email send failed via Resend")
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc

    upsert_draft(badge, content, "submitted")
    return {"message": "Draft submitted and emailed.", "status": "submitted"}


# ═══════════════════════════════════════════════════════════════════════════
# SINGLE AGENT ENTRY — Supervisor-Worker orchestrator
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/chat")
async def chat(request: AgentChatRequest, user: dict = Depends(get_current_user)):
    badge = user["sub"]
    try:
        history = _chat_histories[badge]
        reply, new_history = await run_supervisor(request.message, badge, history)
        _chat_histories[badge] = new_history
        return {"reply": reply}
    except Exception as exc:
        logger.exception("Supervisor agent error")
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc
