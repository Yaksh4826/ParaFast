"""Resend email sender for Para AI reports.

Teddy Bear Form: Completely separate from occurrence. Only 7 fields.
Occurrence Report: Full incident documentation.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict

import resend
from dotenv import load_dotenv
from fpdf import FPDF

logger = logging.getLogger(__name__)
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

# Teddy bear form fields only - nothing from occurrence
TEDDY_FIELDS = ("first_name", "last_name", "medic_number", "timestamp", "recipient_type", "age", "gender")


def _ascii_safe(s: str) -> str:
    """Replace non-ASCII chars so FPDF latin-1 encoding works."""
    if not s:
        return ""
    return str(s).replace("\u2014", "-").replace("\u2013", "-").encode("ascii", "replace").decode("ascii")


def _build_teddy_payload(report: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only teddy bear fields. No occurrence fields."""
    return {k: report.get(k, "") for k in TEDDY_FIELDS}


def _generate_teddy_pdf(data: Dict[str, Any]) -> bytes:
    """Teddy Bear Form - simple layout, only paramedic + recipient."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 14, "Teddy Bear Form", ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 6, "EAI Ambulance Service - Gift Tracking", ln=True, align="C")
    pdf.ln(8)

    pdf.set_font("Arial", "B", 12)
    pdf.set_fill_color(240, 248, 255)
    pdf.cell(0, 8, "  Paramedic", ln=True, fill=True)
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 6, f"  First Name: {_ascii_safe(data.get('first_name', ''))}", ln=True)
    pdf.cell(0, 6, f"  Last Name: {_ascii_safe(data.get('last_name', ''))}", ln=True)
    pdf.cell(0, 6, f"  Medic Number: {_ascii_safe(data.get('medic_number', ''))}", ln=True)
    pdf.cell(0, 6, f"  Timestamp: {_ascii_safe(data.get('timestamp', ''))}", ln=True)
    pdf.ln(4)

    pdf.set_font("Arial", "B", 12)
    pdf.set_fill_color(240, 248, 255)
    pdf.cell(0, 8, "  Recipient", ln=True, fill=True)
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 6, f"  Recipient Type: {_ascii_safe(data.get('recipient_type', ''))}", ln=True)
    pdf.cell(0, 6, f"  Age: {_ascii_safe(data.get('age', ''))}", ln=True)
    pdf.cell(0, 6, f"  Gender: {_ascii_safe(data.get('gender', ''))}", ln=True)

    return pdf.output(dest="S").encode("latin-1")


def _generate_occurrence_pdf(report: Dict[str, Any]) -> bytes:
    """Occurrence Report - full incident layout."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 18)
    pdf.cell(0, 12, "EAI Ambulance Service", ln=True, align="C")
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Occurrence Report", ln=True, align="C")
    pdf.ln(6)

    section_order = [
        ("Report Details", [
            ("Report Creator", "report_creator"),
            ("Badge Number", "badge_number"),
            ("Date/Time", "current_datetime"),
            ("Service Unit", "service_unit"),
            ("Vehicle ID", "vehicle_id"),
            ("Station", "station_name"),
            ("Team", "team_number"),
        ]),
        ("Occurrence", [
            ("Type", "occurrence_type"),
            ("Observation", "observation"),
            ("Actions Taken", "actions_taken"),
            ("Notes", "additional_notes"),
        ]),
    ]

    for section_title, fields in section_order:
        pdf.set_font("Arial", "B", 12)
        pdf.set_fill_color(230, 240, 250)
        pdf.cell(0, 8, f"  {section_title}", ln=True, fill=True)
        pdf.ln(2)
        pdf.set_font("Arial", "", 11)
        for label, key in fields:
            val = report.get(key, "")
            if val:
                pdf.set_font("Arial", "B", 10)
                pdf.cell(55, 7, f"  {label}:")
                pdf.set_font("Arial", "", 10)
                pdf.multi_cell(0, 7, _ascii_safe(str(val)))
                pdf.ln(1)
        pdf.ln(3)

    return pdf.output(dest="S").encode("latin-1")


def _generate_teddy_xml(data: Dict[str, Any]) -> bytes:
    def esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<TeddyBearForm>",
        f"  <FirstName>{esc(data.get('first_name', ''))}</FirstName>",
        f"  <LastName>{esc(data.get('last_name', ''))}</LastName>",
        f"  <MedicNumber>{esc(data.get('medic_number', ''))}</MedicNumber>",
        f"  <Timestamp>{esc(data.get('timestamp', ''))}</Timestamp>",
        f"  <RecipientType>{esc(data.get('recipient_type', ''))}</RecipientType>",
        f"  <Age>{esc(data.get('age', ''))}</Age>",
        f"  <Gender>{esc(data.get('gender', ''))}</Gender>",
        "</TeddyBearForm>",
    ]
    return "\n".join(lines).encode("utf-8")


def _generate_occurrence_xml(report: Dict[str, Any]) -> bytes:
    def esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<OccurrenceReport>"]
    for key, val in report.items():
        lines.append(f"  <{key}>{esc(val)}</{key}>")
    lines.append("</OccurrenceReport>")
    return "\n".join(lines).encode("utf-8")


def _get_from_address() -> str:
    """Use verified domain if set, else Resend sandbox sender."""
    custom = (os.getenv("RESEND_FROM_EMAIL") or "").strip()
    if custom:
        return custom
    return "ParaFast AI <onboarding@resend.dev>"


def send_report_email(report: Dict[str, Any], badge_number: str) -> Dict[str, Any]:
    """Send report via Resend with PDF + XML attachments. Teddy Bear Rule."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    target_email = (os.getenv("TARGET_DISPATCH_EMAIL") or "yakshpatel4826@gmail.com").strip()

    if not api_key:
        logger.error("RESEND_API_KEY not set in .env")
        return {"status": "error", "detail": "RESEND_API_KEY not configured in .env"}

    resend.api_key = api_key
    from_addr = _get_from_address()

    # Teddy: report_type OR presence of recipient_type (kid/child/adult/elderly) = teddy form
    is_teddy = (
        report.get("report_type") == "teddy_bear"
        or (report.get("recipient_type") and not report.get("occurrence_type"))
    )
    logger.info("Sending report to %s (teddy=%s)", target_email, is_teddy)

    if is_teddy:
        data = _build_teddy_payload(report)
        data.setdefault("timestamp", report.get("current_datetime", ""))
        subject = f"Teddy Bear Form - {data.get('first_name', '')} {data.get('last_name', '')}"
        rec = data.get("recipient_type", "")
        age = data.get("age", "")
        gender = data.get("gender", "")
        rec_line = f"Recipient: {rec}" + (f", {age} yrs" if age else "") + (f", {gender}" if gender else "")
        html_body = (
            f"<h2>Teddy Bear Form</h2>"
            f"<p>Hey, here's a teddy bear form from <strong>{data.get('first_name', '')} {data.get('last_name', '')}</strong> (Medic #{data.get('medic_number', '')}).</p>"
            f"<p>{rec_line}</p>"
            f"<p><em>Attached: teddy_bear_form.pdf, teddy_bear_form.xml</em></p>"
        )
        pdf_bytes = _generate_teddy_pdf(data)
        xml_bytes = _generate_teddy_xml(data)
        pdf_fn, xml_fn = "teddy_bear_form.pdf", "teddy_bear_form.xml"
    else:
        creator = report.get("report_creator", report.get("full_name", badge_number))
        ts = report.get("current_datetime", "")
        subject = f"Occurrence Report - {creator} ({badge_number}) - {ts}"
        html_body = (
            f"<h2>Occurrence Report</h2>"
            f"<p>Submitted by <strong>{creator}</strong> (Badge: {badge_number})</p>"
            f"<p><strong>Type:</strong> {report.get('occurrence_type', 'N/A')}<br>"
            f"<strong>Vehicle:</strong> {report.get('vehicle_id', 'N/A')}</p>"
            f"<p>Attached: occurrence_report.pdf, occurrence_report.xml</p>"
        )
        pdf_bytes = _generate_occurrence_pdf(report)
        xml_bytes = _generate_occurrence_xml(report)
        pdf_fn, xml_fn = "occurrence_report.pdf", "occurrence_report.xml"

    try:
        resp = resend.Emails.send({
            "from": from_addr,
            "to": [target_email],
            "subject": subject,
            "html": html_body,
            "attachments": [
                {"filename": pdf_fn, "content": base64.b64encode(pdf_bytes).decode()},
                {"filename": xml_fn, "content": base64.b64encode(xml_bytes).decode()},
            ],
        })
        email_id = getattr(resp, "id", None) or (resp.get("id") if isinstance(resp, dict) else None)
        logger.info("Email sent successfully to %s (id=%s)", target_email, email_id)
        return {"status": "sent", "to": target_email, "id": email_id}
    except Exception as exc:
        logger.exception("Resend send failed: %s", exc)
        return {"status": "error", "detail": str(exc)}
