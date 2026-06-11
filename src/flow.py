"""Flow screen routing logic for the appointment template."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from src.audio_service import AI_TEXT_PROMPT, generate_answer

# ─── Load all reference data from data/ ──────────────────────────────────────

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load(filename: str):
    with open(_DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


_DEPARTMENTS:  list[dict]      = _load("departments.json")
_DISTRICTS:    list[dict]      = _load("districts.json")
_TIME_SLOTS:   list[dict]      = _load("time_slots.json")
HOSPITAL_DATA: dict[str, dict] = _load("hospitals.json")
OPD_INFO:      dict[str, dict] = _load("opd_info.json")

_ALL_HOSPITALS = [{"id": hid, "title": h["name"]} for hid, h in HOSPITAL_DATA.items()]

# ─── Screen response templates ───────────────────────────────────────────────

SCREEN_RESPONSES = {
    "SERVICES": {
        "screen": "SERVICES",
        "data": {},
    },

    "APPOINTMENT": {
        "screen": "APPOINTMENT",
        "data": {
            "department":          _DEPARTMENTS,
            "district":            _DISTRICTS,
            "is_district_enabled": False,
            "hospital":            _ALL_HOSPITALS,
            "is_hospital_enabled": False,
            "date":                [],
            "is_date_enabled":     False,
            "time":                _TIME_SLOTS,
            "is_time_enabled":     False,
        },
    },

    "DETAILS": {
        "screen": "DETAILS",
        "data": {
            "department": "cardiology",
            "district": "hyderabad",
            "hospital": "ogh",
            "date": "2026-06-06",
            "time": "10:30",
        },
    },

    "SUMMARY": {
        "screen": "SUMMARY",
        "data": {
            "appointment": "Cardiology at Osmania General Hospital, Hyderabad\nSat Jun 06 2026 at 10:30 AM.",
            "details": "Name: John Doe\nEmail: john@example.com\nPhone: 123456789",
            "department": "cardiology",
            "district": "hyderabad",
            "hospital": "ogh",
            "date": "2026-06-06",
            "time": "10:30",
            "name": "John Doe",
            "email": "john@example.com",
            "phone": "123456789",
            "more_details": "",
        },
    },

    "TERMS": {
        "screen": "TERMS",
        "data": {},
    },

    "DOCTOR_OPD": {
        "screen": "DOCTOR_OPD",
        "data": {
            "department":          _DEPARTMENTS,
            "hospital":            _ALL_HOSPITALS,
            "is_hospital_enabled": False,
        },
    },

    "DOCTOR_OPD_RESULT": {
        "screen": "DOCTOR_OPD_RESULT",
        "data": {
            "result_text": "OPD Information\nPlease contact the hospital for the latest timings.",
            "department": "",
            "hospital": "",
        },
    },

    "LAB_REPORTS": {
        "screen": "LAB_REPORTS",
        "data": {},
    },

    "LAB_REPORT_VIEW": {
        "screen": "LAB_REPORT_VIEW",
        "data": {
            "report_summary": "No reports found for the provided details.",
            "patient_name": "",
        },
    },

    "AI_ASSISTANCE": {
        "screen": "AI_ASSISTANCE",
        "data": {},
    },

    "AI_RESPONSE": {
        "screen": "AI_RESPONSE",
        "data": {
            "ai_response": (
                "Thank you for sharing your health query.\n\n"
                "Please consult a qualified doctor for medical advice.\n"
                "This is general health information only.\n\n"
                "⚠️ Disclaimer: This is not a substitute for professional medical advice."
            ),
        },
    },

    "HOSPITAL_INFO": {
        "screen": "HOSPITAL_INFO",
        "data": {
            "hospitals": [],
        },
    },

    "EMERGENCY_VIDEOS": {
        "screen": "EMERGENCY_VIDEOS",
        "data": {},
    },

    "AUDIO_GUIDANCE": {
        "screen": "AUDIO_GUIDANCE",
        "data": {},
    },

    "HOSPITAL_DETAIL": {
        "screen": "HOSPITAL_DETAIL",
        "data": {
            "hospital_name": "",
            "address": "",
            "phone": "",
            "specialties": "",
        },
    },

    "SUCCESS": {
        "screen": "SUCCESS",
        "data": {
            "extension_message_response": {
                "params": {
                    "flow_token": "REPLACE_FLOW_TOKEN",
                    "some_param_name": "PASS_CUSTOM_VALUE",
                }
            }
        },
    },
}


def _get_hospital_name(hospital_id: str) -> str:
    hosp = HOSPITAL_DATA.get(hospital_id)
    return hosp["name"] if hosp else hospital_id.replace("_", " ").title()


def _build_opd_result_brief(department_id: str, hospital_id: str) -> str:
    """Compact result shown inside the flow screen (no doctors/timings)."""
    dept = OPD_INFO.get(department_id)
    dept_name = dept["name"] if dept else department_id.replace("_", " ").title()
    hospital_name = _get_hospital_name(hospital_id)
    return (
        f"🏥 {dept_name} OPD\n"
        f"Hospital: {hospital_name}\n\n"
        f"Full OPD details (timings & doctors) will be\n"
        f"sent to your WhatsApp."
    )


def _build_opd_result(department_id: str, hospital_id: str) -> str:
    """Full OPD result text sent via WhatsApp message."""
    dept = OPD_INFO.get(department_id)
    if not dept:
        return "Department information not available. Please contact the hospital directly."

    hospital_name = _get_hospital_name(hospital_id)
    doctors_text = "\n".join(f"  • {d}" for d in dept["doctors"])
    return (
        f"🏥 {dept['name']} OPD\n"
        f"Hospital: {hospital_name}\n\n"
        f"⏰ OPD Timings:\n  {dept['timings']}\n\n"
        f"👨‍⚕️ Doctors:\n{doctors_text}"
    )


_AI_FALLBACK = (
    "Unable to process your query right now.\n\n"
    "Please visit your nearest hospital or contact your doctor.\n\n"
    "🚨 For emergencies, call 108 immediately.\n\n"
    "⚠️ Consult a doctor for proper diagnosis."
)


def _get_next_7_days() -> list[dict[str, str]]:
    import datetime
    today = datetime.date.today()
    dates = []
    for i in range(7):
        d = today + datetime.timedelta(days=i)
        dates.append({
            "id": d.strftime("%Y-%m-%d"),
            "title": d.strftime("%a %b %d %Y")
        })
    return dates


def _get_filtered_time_slots(selected_date_str: str | None) -> list[dict[str, str]]:
    import datetime

    if not selected_date_str:
        return _TIME_SLOTS

    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    if selected_date_str != today_str:
        return _TIME_SLOTS

    filtered_slots = []
    limit_time = now + datetime.timedelta(hours=1)
    limit_hour = limit_time.hour
    limit_minute = limit_time.minute

    for slot in _TIME_SLOTS:
        slot_id = slot["id"]
        try:
            sh, sm = map(int, slot_id.split(":"))
            if (sh > limit_hour) or (sh == limit_hour and sm >= limit_minute):
                filtered_slots.append(slot)
        except ValueError:
            filtered_slots.append(slot)
    return filtered_slots


def _get_hospital_list_for_info() -> list[dict]:
    return [
        {
            "id": f"{hid},{hdata['district_id']}",
            "title": f"{hdata['name']}, {hdata['district']}",
        }
        for hid, hdata in HOSPITAL_DATA.items()
    ]


async def get_next_screen(decrypted_body: dict[str, Any]) -> dict[str, Any]:
    SCREEN_RESPONSES["APPOINTMENT"]["data"]["date"] = _get_next_7_days()

    screen = decrypted_body.get("screen")
    data = decrypted_body.get("data") or {}
    action = decrypted_body.get("action")
    flow_token = decrypted_body.get("flow_token")

    if action == "ping":
        return {"data": {"status": "active"}}

    if data.get("error"):
        return {"data": {"acknowledged": True}}

    # ── INIT: show the Services menu ─────────────────────────────────────────
    if action == "INIT":
        return SCREEN_RESPONSES["SERVICES"]

    if action == "data_exchange":

        # ── SERVICES SCREEN: route user to chosen service ─────────────────
        if screen == "SERVICES":
            service = data.get("service")

            if service == "APPOINTMENT":
                return {
                    **SCREEN_RESPONSES["APPOINTMENT"],
                    "data": {
                        **SCREEN_RESPONSES["APPOINTMENT"]["data"],
                        "is_district_enabled": True,
                        "is_hospital_enabled": True,
                        "is_date_enabled": False,
                        "is_time_enabled": False,
                        "hospital": _ALL_HOSPITALS,
                        "time": _get_filtered_time_slots(None),
                    },
                }

            if service == "DOCTOR_OPD":
                return {
                    **SCREEN_RESPONSES["DOCTOR_OPD"],
                    "data": {
                        **SCREEN_RESPONSES["DOCTOR_OPD"]["data"],
                        "is_hospital_enabled": False,
                    },
                }

            if service == "LAB_REPORTS":
                return SCREEN_RESPONSES["LAB_REPORTS"]

            if service == "AI_ASSISTANCE":
                return SCREEN_RESPONSES["AI_ASSISTANCE"]

            if service == "HOSPITAL_INFO":
                return {
                    **SCREEN_RESPONSES["HOSPITAL_INFO"],
                    "data": {
                        "hospitals": _get_hospital_list_for_info(),
                    },
                }

            if service == "EMERGENCY_VIDEOS":
                return SCREEN_RESPONSES["EMERGENCY_VIDEOS"]

            if service == "AUDIO_GUIDANCE":
                return SCREEN_RESPONSES["AUDIO_GUIDANCE"]

        # ── APPOINTMENT SCREEN ────────────────────────────────────────────
        if screen == "APPOINTMENT":

            district_id = data.get("district") or data.get("location")
            hospital_selected = bool(data.get("hospital"))
            date_selected = bool(data.get("date"))

            if district_id:
                filtered_hospitals = [
                    {"id": hid, "title": hdata["name"]}
                    for hid, hdata in HOSPITAL_DATA.items()
                    if hdata["district_id"] == district_id
                ]
            else:
                filtered_hospitals = _ALL_HOSPITALS

            return {
                **SCREEN_RESPONSES["APPOINTMENT"],
                "data": {
                    **SCREEN_RESPONSES["APPOINTMENT"]["data"],
                    "is_district_enabled": True,
                    "is_hospital_enabled": True,
                    "is_date_enabled": hospital_selected,
                    "is_time_enabled": hospital_selected and date_selected,
                    "district": _DISTRICTS,
                    "hospital": filtered_hospitals,
                    "date": SCREEN_RESPONSES["APPOINTMENT"]["data"]["date"],
                    "time": _get_filtered_time_slots(data.get("date")),
                },
            }

        # ── DETAILS SCREEN ────────────────────────────────────────────────
        if screen == "DETAILS":

            department_name = next(
                dept["title"]
                for dept in _DEPARTMENTS
                if dept["id"] == data.get("department")
            )

            district_name = next(
                district["title"]
                for district in _DISTRICTS
                if district["id"] == data.get("district")
            )

            hospital_name = (
                HOSPITAL_DATA.get(data.get("hospital", ""), {}).get("name")
                or data.get("hospital", "Unknown")
            )

            date_name = next(
                date["title"]
                for date in SCREEN_RESPONSES["APPOINTMENT"]["data"]["date"]
                if date["id"] == data.get("date")
            )

            appointment = (
                f"{department_name}\n"
                f"District: {district_name}\n"
                f"Hospital: {hospital_name}\n"
                f"{date_name} at {data.get('time')}"
            )

            details_parts = [f"Name: {data.get('name', '')}"]
            email_val = data.get('email')
            if email_val and email_val.strip():
                details_parts.append(f"Email: {email_val.strip()}")
            details_parts.append(f"Phone: {data.get('phone', '')}")

            more_details = data.get('more_details')
            if more_details and more_details.strip():
                details_parts.append(f"\n{more_details.strip()}")

            details = "\n".join(details_parts)

            return {
                **SCREEN_RESPONSES["SUMMARY"],
                "data": {
                    "appointment": appointment,
                    "details": details,
                    **data,
                },
            }

        # ── SUMMARY SCREEN ────────────────────────────────────────────────
        if screen == "SUMMARY":
            department_id = data.get("department")
            hospital_id = data.get("hospital")
            date_id = data.get("date")
            time_id = data.get("time")

            department_name = next(
                (dept["title"] for dept in _DEPARTMENTS if dept["id"] == department_id),
                department_id or "Unknown"
            )
            hospital_name = (
                HOSPITAL_DATA.get(hospital_id or "", {}).get("name")
                or hospital_id or "Unknown"
            )
            district_id = data.get("district")
            district_name = next(
                (dist["title"] for dist in _DISTRICTS if dist["id"] == district_id),
                district_id or "Unknown"
            )

            import datetime
            formatted_date = date_id or "Unknown"
            try:
                parsed_date = datetime.datetime.strptime(date_id, "%Y-%m-%d")
                formatted_date = f"{parsed_date.day} {parsed_date.strftime('%B %Y')}"
            except Exception:
                pass

            time_name = next(
                (t["title"] for t in _TIME_SLOTS if t["id"] == time_id),
                time_id or "Unknown"
            )
            if time_name.startswith("0"):
                time_name = time_name[1:]

            return {
                **SCREEN_RESPONSES["SUCCESS"],
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            "service": "appointment",
                            "department": department_name,
                            "hospital": hospital_name,
                            "district": district_name,
                            "date": formatted_date,
                            "time": time_name,
                            "name": data.get("name", ""),
                            "phone": data.get("phone", ""),
                        }
                    }
                },
            }

        # ── DOCTOR_OPD SCREEN ─────────────────────────────────────────────
        if screen == "DOCTOR_OPD":
            trigger = data.get("trigger")

            if trigger == "opd_department_selected":
                dept_selected = bool(data.get("department"))
                return {
                    **SCREEN_RESPONSES["DOCTOR_OPD"],
                    "data": {
                        **SCREEN_RESPONSES["DOCTOR_OPD"]["data"],
                        "is_hospital_enabled": dept_selected,
                    },
                }

            if trigger == "find_doctors":
                department_id = data.get("department", "")
                hospital_id = data.get("hospital", "")
                brief_text = _build_opd_result_brief(department_id, hospital_id)
                return {
                    **SCREEN_RESPONSES["DOCTOR_OPD_RESULT"],
                    "data": {
                        "result_text": brief_text,
                        "department": department_id,
                        "hospital": hospital_id,
                    },
                }

        # ── DOCTOR_OPD_RESULT SCREEN (Done button) ────────────────────────
        if screen == "DOCTOR_OPD_RESULT":
            department_id = data.get("department", "")
            hospital_id = data.get("hospital", "")
            full_result = _build_opd_result(department_id, hospital_id)
            return {
                **SCREEN_RESPONSES["SUCCESS"],
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            "service": "opd",
                            "result_text": full_result,
                        }
                    }
                },
            }

        # ── LAB_REPORTS SCREEN ────────────────────────────────────────────
        if screen == "LAB_REPORTS":
            patient_id = data.get("patient_id", "").strip()
            dob_raw = data.get("dob", "").strip()

            try:
                import datetime as _dt
                dob_display = _dt.datetime.strptime(dob_raw, "%Y-%m-%d").strftime("%d %B %Y")
            except Exception:
                dob_display = dob_raw

            report_summary = (
                f"Patient ID: {patient_id}\n"
                f"Date of Birth: {dob_display}\n\n"
                f"📋 Report Status: Available\n\n"
                f"Complete Blood Count (CBC)\n"
                f"  • Haemoglobin: 13.5 g/dL  ✅ Normal\n"
                f"  • WBC: 7,200 /µL            ✅ Normal\n"
                f"  • Platelets: 2.5 Lac /µL   ✅ Normal\n\n"
                f"Blood Sugar (Fasting)\n"
                f"  • Glucose: 98 mg/dL         ✅ Normal\n\n"
                f"For complete reports, visit your nearest hospital."
            )

            return {
                **SCREEN_RESPONSES["LAB_REPORT_VIEW"],
                "data": {
                    "report_summary": report_summary,
                    "patient_name": f"Patient #{patient_id}",
                },
            }

        # ── LAB_REPORT_VIEW SCREEN (Done button) ──────────────────────────
        if screen == "LAB_REPORT_VIEW":
            return {
                **SCREEN_RESPONSES["SUCCESS"],
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            "service": "lab",
                        }
                    }
                },
            }

        # ── HOSPITAL_INFO SCREEN ──────────────────────────────────────────
        if screen == "HOSPITAL_INFO":
            hospital_key = data.get("hospital", "")
            hospital_id = hospital_key.split(",", 1)[0]
            hosp = HOSPITAL_DATA.get(hospital_id)
            if hosp:
                return {
                    **SCREEN_RESPONSES["HOSPITAL_DETAIL"],
                    "data": {
                        "hospital_name": hosp["name"],
                        "address": hosp["address"],
                        "phone": hosp["phone"],
                        "specialties": hosp["specialties"],
                    },
                }
            return {
                **SCREEN_RESPONSES["HOSPITAL_DETAIL"],
                "data": {
                    "hospital_name": "Hospital Not Found",
                    "address": "N/A",
                    "phone": "N/A",
                    "specialties": "N/A",
                },
            }

        # ── EMERGENCY_VIDEOS SCREEN ───────────────────────────────────────
        if screen == "EMERGENCY_VIDEOS":
            return {
                **SCREEN_RESPONSES["SUCCESS"],
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            "service": "emergency_video",
                            "video_id": data.get("video_id", ""),
                        }
                    }
                },
            }

        # ── AUDIO_GUIDANCE SCREEN (Confirm button) ────────────────────────
        # Completing the flow here makes the webhook send the "send your query
        # through audio" prompt; the user then replies with a voice note which
        # src/audio_service.py answers with an audio reply.
        if screen == "AUDIO_GUIDANCE":
            return {
                **SCREEN_RESPONSES["SUCCESS"],
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            "service": "audio_guidance",
                        }
                    }
                },
            }

        # ── AI_ASSISTANCE SCREEN ──────────────────────────────────────────
        # Complete the flow immediately (same pattern as OPD/Lab/Hospital).
        # The LLM response travels via extension_message_response.params →
        # nfm_reply → server.py which sends it as a WhatsApp message.
        # This works with the currently published flow.json and avoids any
        # dependency on flow.json republishing.
        if screen == "AI_ASSISTANCE":
            query = data.get("query", "").strip()
            ai_response = await asyncio.to_thread(
                generate_answer, query, AI_TEXT_PROMPT
            )
            ai_response = ai_response or _AI_FALLBACK
            return {
                **SCREEN_RESPONSES["SUCCESS"],
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            "service": "ai",
                            "ai_response": ai_response,
                        }
                    }
                },
            }

    raise RuntimeError(
        "Unhandled endpoint request. Make sure you handle the request action & screen."
    )
