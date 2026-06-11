"""FastAPI entrypoint for the WhatsApp Flows endpoint server."""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import httpx

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from src.audio_service import AUDIO_PROMPT_MESSAGE, handle_audio_query
from src.encryption import FlowEndpointException, decrypt_request, encrypt_response
from src.flow import get_next_screen
from src.webhook_utils import get_first_message, get_value


load_dotenv()

app = FastAPI(title="WhatsApp Flows Endpoint")

PORT                 = int(os.getenv("PORT", "3000"))
APP_SECRET           = os.getenv("APP_SECRET", "7738af0498e44f879eb5c93a5812729c")
PASSPHRASE      = os.getenv("PASSPHRASE", "-encodedCommand")
PRIVATE_KEY     = os.getenv("PRIVATE_KEY")
GRAPH_API_TOKEN = os.getenv("GRAPH_API_TOKEN")
FLOW_ID              = os.getenv("FLOW_ID")
FLOW_TOKEN           = os.getenv("flow_token", "flows-builder-token")

# ── THS access control ───────────────────────────────────────────────────────
# THS replies only to events delivered to its own WhatsApp number, and (during
# rollout) only to a whitelist of test senders. Set THS_ALLOWED_NUMBERS="" to
# allow every sender once the service goes live.
THS_PHONE_NUMBER_ID = os.getenv("THS_PHONE_NUMBER_ID", "1150422371484965").strip()
THS_ALLOWED_NUMBERS = {
    n.strip()
    for n in os.getenv("THS_ALLOWED_NUMBERS").split(",")
    if n.strip()
}

# ── Emergency video library: id → {url, caption} ─────────────────────────────
# Replace the URLs below with the actual S3 / CDN links for each guide.
EMERGENCY_VIDEO_LIBRARY: dict[str, dict[str, str]] = {
    "cpr": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/VID-20260607-WA0001.mp4?alt=media&token=67bc15ff-d426-46da-9b6d-4c3a08ededc3",
        "caption": "CPR – 30 chest compressions + 2 rescue breaths. Call 108.",
    },
    "snake_bite": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/teja%2FVID-20260607-WA0004.mp4?alt=media&token=4dab2485-f318-49c8-8e21-28a08af58c08",
        "caption": "Snake Bite – Stay calm, immobilise the limb, seek help immediately.",
    },
    "burn": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/teja%2FVID-20260607-WA0006.mp4?alt=media&token=00c3f23a-26ad-4509-8972-8e9f1110d477",
        "caption": "Burn Management – Cool with running water for 10 min. Do not use ice.",
    },
    "fracture": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/teja%2FVID-20260607-WA0007.mp4?alt=media&token=c41ee6d4-25fe-4828-ab52-70bc4b380fd8",
        "caption": "Fracture – Immobilise the area. Do not attempt to realign the bone.",
    },
    "electric_shock": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/teja%2FVID-20260607-WA0003.mp4?alt=media&token=a02b1c41-c2e6-4f1b-95ac-9638fbde84bc",
        "caption": "Electric Shock – Cut power first. Do not touch the victim directly.",
    },
    "bites_stings": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/teja%2FVID-20260607-WA0005.mp4?alt=media&token=b6468824-3e12-4c5d-902a-fa173014d0ee",
        "caption": "Bites & Stings – Clean the wound, remove stinger if visible, watch for allergic reaction.",
    },
    "choking": {
        "url":     "https://firebasestorage.googleapis.com/v0/b/quantumads-verify.firebasestorage.app/o/teja%2FVID-20260607-WA0002.mp4?alt=media&token=d734219f-0180-44e8-a043-613f21be22aa",
        "caption": "Choking – 5 back blows + 5 abdominal thrusts. Call 108 if not resolved.",
    },
}


def is_request_signature_valid(raw_body: bytes, signature_header: str | None) -> bool:
    if not APP_SECRET:
        return True

    if not signature_header:
        return False

    received_signature = signature_header.removeprefix("sha256=").lower()
    expected_signature = hmac.new(APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, received_signature):
        return False
    return True


@app.post("/")
async def flow_endpoint(request: Request) -> Response:
    if not PRIVATE_KEY:
        raise RuntimeError('Private key is empty. Please check your env variable "PRIVATE_KEY".')

    raw_body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256")
    if not is_request_signature_valid(raw_body, signature_header):
        return Response(status_code=432)

    try:
        body = await request.json()
        decrypted_request = decrypt_request(body, PRIVATE_KEY, PASSPHRASE)
    except FlowEndpointException as exc:
        return Response(status_code=exc.status_code)
    except Exception:
        return Response(status_code=500)

    aes_key_buffer = decrypted_request["aesKeyBuffer"]
    initial_vector_buffer = decrypted_request["initialVectorBuffer"]
    decrypted_body = decrypted_request["decryptedBody"]

    screen_response = await get_next_screen(decrypted_body)

    encrypted = encrypt_response(screen_response, aes_key_buffer, initial_vector_buffer)
    return Response(content=encrypted, media_type="text/plain")


@app.get("/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("OK")


@app.get("/")
async def root() -> PlainTextResponse:
    return PlainTextResponse("""Nothing to see here.
Checkout README.md to start.""".strip())


# ── Webhook event handler (plain JSON forwarded by main.py) ──────────────────

@app.post("/webhook")
async def webhook_receiver(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Handle plain WhatsApp webhook events forwarded by the router (main.py).

    • text message  → sends the interactive Flow message to the user
    • audio message → answers the voice query with a voice reply (Audio Guidance)
    • nfm_reply     → sends a service-specific completion message
    • always        → marks the message as read
    """
    payload = await request.json()

    try:
        value           = get_value(payload)
        phone_number_id = value.get("metadata", {}).get("phone_number_id", "").strip()
        message         = get_first_message(payload)
        sender          = message.get("from", "")

        # ── Respond only to events delivered to THS's own number ─────────
        if message:
            if THS_PHONE_NUMBER_ID and phone_number_id != THS_PHONE_NUMBER_ID:
                return Response(status_code=200)
            if THS_ALLOWED_NUMBERS and sender not in THS_ALLOWED_NUMBERS:
                return Response(status_code=200)

        if message and phone_number_id:
            async with httpx.AsyncClient(timeout=30) as client:
                graph_url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
                headers   = {"Authorization": f"Bearer {GRAPH_API_TOKEN}"}

                # ── Text message → trigger the Flow ──────────────────────
                if message.get("type") == "text":
                    await client.post(
                        graph_url,
                        headers=headers,
                        json={
                            "messaging_product": "whatsapp",
                            "to": message.get("from"),
                            "type": "interactive",
                            "interactive": {
                                "type": "flow",
                                "header": {"type": "text", "text": "👋 I'm THS Virtual Assistant"},
                                "body": {
                                    "text": (
                                        "I can assist you with booking appointments, finding doctors, accessing lab reports, and providing AI-powered health guidance."
                                    )
                                },
                                "footer": {"text": "Tap below to get started"},
                                "action": {
                                    "name": "flow",
                                    "parameters": {
                                        "flow_id": FLOW_ID,
                                        "flow_message_version": "3",
                                        "flow_token": FLOW_TOKEN,
                                        "flow_cta": "Choose Service",
                                        "flow_action": "data_exchange"
                                    },
                                },
                            },
                        },
                    )

                # ── audio message → Audio Guidance voice reply ─────────────
                # Any voice note is treated as an Audio Guidance query (the
                # user reaches this after confirming the service in the Flow).
                # Processing runs in the background so we can return 200 to Meta
                # immediately — the STT → LLM → TTS round-trip takes seconds.
                if message.get("type") == "audio":
                    media_id = message.get("audio", {}).get("id")
                    if media_id:
                        background_tasks.add_task(
                            handle_audio_query,
                            media_id,
                            message.get("from", ""),
                            phone_number_id,
                        )

                # ── nfm_reply → service-specific completion message ────────
                if (
                    message.get("type") == "interactive"
                    and message.get("interactive", {}).get("type") == "nfm_reply"
                ):
                    nfm_reply     = message["interactive"]["nfm_reply"]
                    response_json = nfm_reply.get("response_json", "{}")
                    try:
                        flow_data = json.loads(response_json)
                    except Exception:
                        flow_data = {}

                    service      = flow_data.get("service", "")
                    confirm_body = None  # set by each branch; None means no text reply

                    if service == "appointment":
                        department = flow_data.get("department", "Unknown")
                        hospital   = flow_data.get("hospital",   "Unknown")
                        district   = flow_data.get("district",   "Unknown")
                        date       = flow_data.get("date",       "Unknown")
                        time       = flow_data.get("time",       "Unknown")
                        name       = flow_data.get("name",       "")
                        phone      = flow_data.get("phone",      "")
                        confirm_body = (
                            "✅ Appointment Confirmed!\n\n"
                            f"👤 Patient: {name}\n"
                            f"📞 Phone: {phone}\n\n"
                            f"🏥 Department: {department}\n"
                            f"🏨 Hospital: {hospital}\n"
                            f"📍 District: {district}\n\n"
                            f"📅 Date: {date}\n"
                            f"⏰ Time: {time}"
                        )

                    elif service == "opd":
                        result_text  = flow_data.get("result_text", "OPD information not available.")
                        confirm_body = f"🏥 OPD Information:\n\n{result_text}"

                    elif service == "lab":
                        confirm_body = (
                            "✅ Lab Report Ready!\n"
                            "Your lab report has been successfully generated. Visit your nearest hospital or contact the lab to collect your report."
                        )

                    elif service == "ai":
                        confirm_body = flow_data.get("ai_response") or "Thank you for using AI Health Assistance."

                    elif service == "audio_guidance":
                        # Prompt the user to send a voice note; their reply is
                        # handled by the "audio message" branch above.
                        confirm_body = AUDIO_PROMPT_MESSAGE

                    elif service == "emergency_video":
                        video_id = flow_data.get("video_id", "")
                        video    = EMERGENCY_VIDEO_LIBRARY.get(video_id)
                        if video:
                            await client.post(
                                graph_url,
                                headers=headers,
                                json={
                                    "messaging_product": "whatsapp",
                                    "recipient_type": "individual",
                                    "to": message.get("from"),
                                    "type": "video",
                                    "video": {
                                        "link":    video["url"],
                                        "caption": video["caption"],
                                    },
                                },
                            )
                            # confirm_body stays None — video message already sent
                        else:
                            confirm_body = "⚠️ Emergency video not found. Please try again."

                    elif service == "hospital_info":
                        hospital_name = flow_data.get("hospital_name", "Unknown")
                        address       = flow_data.get("address", "Unknown")
                        phone         = flow_data.get("phone", "Unknown")
                        specialties   = flow_data.get("specialties", "Unknown")
                        confirm_body  = (
                            f"🏥 *{hospital_name}*\n\n"
                            f"📍 *Address:* {address}\n"
                            f"📞 *Phone:* {phone}\n"
                            f"🩺 *Specialties:* {specialties}"
                        )

                    else:
                        hospital     = flow_data.get("hospital", "Unknown")
                        district     = flow_data.get("district", "Unknown")
                        date         = flow_data.get("date",     "Unknown")
                        time         = flow_data.get("time",     "Unknown")
                        confirm_body = (
                            "✅ Request Received!\n\n"
                            f"Hospital: {hospital}, {district}\n"
                            f"Date: {date}\n"
                            f"Time: {time}"
                        )

                    if confirm_body is not None:
                        await client.post(
                            graph_url,
                            headers=headers,
                            json={
                                "messaging_product": "whatsapp",
                                "type": "text",
                                "to": message.get("from"),
                                "text": {"body": confirm_body},
                            },
                        )

                # ── Mark message as read ──────────────────────────────────
                await client.post(
                    graph_url,
                    headers=headers,
                    json={
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": message.get("id"),
                    },
                )

    except Exception:
        pass

    return Response(status_code=200)


def main() -> None:
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)


if __name__ == "__main__":
    main()