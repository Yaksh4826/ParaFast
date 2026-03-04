import asyncio
import os
import re
from datetime import datetime
from typing import Any, Dict

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def _normalize_time(raw: str) -> str:
    """Convert various time formats to HH:MM (24-hour) for the HTML time input.

    Accepts: "01:30:PM", "1:30 PM", "01:30PM", "13:30", "7:00:AM", etc.
    Returns: "13:30", "07:00", etc.
    """
    raw = raw.strip().upper()
    raw = re.sub(r"[:\s]+(?=[AP]M)", " ", raw)
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return raw


def _fill_shift_request_form_sync(
    form_url: str | None,
    first_name: str,
    last_name: str,
    medic_number: str,
    shift_date: str,
    start_time: str,
    end_time: str,
    requested_action: str,
    reason: str | None = None,
) -> Dict[str, Any]:
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    url = form_url or os.getenv("SHIFT_REQUEST_FORM_URL")
    if not url:
        return {"success": False, "error": "Missing form URL."}

    valid_actions = {"Day Off Request", "Swap Shift", "Vacation Day", "Other"}
    if requested_action not in valid_actions:
        return {
            "success": False,
            "error": f"Invalid action '{requested_action}'. Must be one of: {', '.join(sorted(valid_actions))}",
        }

    start_24 = _normalize_time(start_time)
    end_24 = _normalize_time(end_time)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)

            page.fill("#first-name", first_name)
            page.fill("#last-name", last_name)
            page.fill("#medic-number", medic_number)
            page.fill("#shift-day", shift_date)
            page.fill("#shift-start", start_24)
            page.fill("#shift-end", end_24)
            page.select_option("#action", value=requested_action)

            if requested_action == "Other" and reason:
                page.wait_for_selector("#reason-group.visible", timeout=3_000)
                page.fill("#reason", reason)

            page.click("button.submit-btn")

            page.wait_for_selector("#success-banner", state="visible", timeout=10_000)
            return {
                "success": True,
                "message": (
                    f"Shift change request submitted for {first_name} {last_name} ({medic_number}). "
                    f"The email dialog has been opened — please send the email to complete your submission."
                ),
            }
        except PlaywrightTimeoutError:
            return {"success": False, "error": "Form submission timed out waiting for confirmation banner."}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        finally:
            browser.close()


async def fill_shift_request_form(
    form_url: str | None,
    first_name: str,
    last_name: str,
    medic_number: str,
    shift_date: str,
    start_time: str,
    end_time: str,
    requested_action: str,
    reason: str | None = None,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _fill_shift_request_form_sync,
        form_url, first_name, last_name, medic_number,
        shift_date, start_time, end_time, requested_action, reason,
    )
