import base64
import os
import logging
from typing import Any, Dict, Optional

from dicttoxml import dicttoxml
from fastapi import FastAPI, HTTPException
from fpdf import FPDF
from pydantic import ValidationError
import resend

# Support both package-style imports (backend.main) and running as a top-level module (main.py)
if __package__ is None or __package__ == "":
    import pathlib
    import sys

    sys.path.append(str(pathlib.Path(__file__).resolve().parent))
    from database import get_supabase_client  # type: ignore
    from schemas import OccurrenceReport, UpdateDraftRequest  # type: ignore
else:
    from .database import get_supabase_client
    from .schemas import OccurrenceReport, UpdateDraftRequest

app = FastAPI(title="EMS Form-to-Email Service")

logger = logging.getLogger(__name__)

supabase = get_supabase_client()


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
            "updated_at": "now()",
        },
        on_conflict="badge_number",
    ).execute()


def generate_xml_content(data: Dict[str, Any]) -> bytes:
    return dicttoxml(data or {}, custom_root="occurrence_report", attr_type=False)


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
            line = f"{key}: {value}"
            pdf.multi_cell(0, 8, line)
            pdf.ln(1)

    return pdf.output(dest="S").encode("latin-1")


def send_email_with_attachments(
    to_email: str, badge_number: str, xml_bytes: bytes, pdf_bytes: bytes
) -> None:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY is not configured.")

    # Configure Resend client
    resend.api_key = api_key
    attachments = [
        {
            "filename": "occurrence_report.xml",
            "content": base64.b64encode(xml_bytes).decode(),
        },
        {
            "filename": "occurrence_report.pdf",
            "content": base64.b64encode(pdf_bytes).decode(),
        },
    ]

    resend.Emails.send(
        {
            "from": "ParaFast AI <onboarding@resend.dev>",
            "to": [to_email],
            "subject": f"Occurrence Report for badge {badge_number}",
            "html": "<p>A new occurrence report has been submitted. Please see the attached XML and PDF.</p>",
            "attachments": attachments,
        }
    )


@app.post("/update_draft")
async def update_draft(request: UpdateDraftRequest):
    draft = fetch_draft(request.badge_number)
    existing_content = draft.get("content") if draft else {}
    merged_content = merge_content(existing_content, request.patch)
    status = draft.get("status") if draft else "draft"

    upsert_draft(request.badge_number, merged_content, status)

    return {
        "badge_number": request.badge_number,
        "status": status,
        "content": merged_content,
    }


@app.post("/submit_and_email")
async def submit_and_email(badge_number: str):
    """
    Accepts badge_number as a query parameter so FastAPI does not expect a JSON body.
    """
    draft = fetch_draft(badge_number)
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
        raise HTTPException(
            status_code=500, detail="TARGET_DISPATCH_EMAIL is not configured."
        )

    try:
        send_email_with_attachments(target_email, badge_number, xml_bytes, pdf_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Email send failed via Resend")
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc

    upsert_draft(badge_number, content, "submitted")

    return {"message": "Draft submitted and emailed.", "status": "submitted"}
