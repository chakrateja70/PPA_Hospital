"""WhatsApp webhook router.

Dispatches incoming Meta webhook events to downstream services by phone_number_id:

  THS_PHONE_NUMBER_ID  →  THS_WEBHOOK_URL
  APMC_PHONE_NUMBER_ID  →  APMC_WEBHOOK_URL

Add more services by adding new env-var pairs to _routing_pairs below.
"""

from __future__ import annotations

import json
import os

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from src.webhook_utils import get_phone_number_id

load_dotenv()

app = FastAPI(title="WhatsApp Webhook Router")

PORT                 = int(os.getenv("PORT", "9000"))
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN")

# ── Phone-number → downstream service routing ─────────────────────────────────
PHONE_TO_SERVICE: dict[str, dict] = {}

_routing_pairs = [
    ("THS_PHONE_NUMBER_ID", "THS_WEBHOOK_URL", "THS"),
    ("APMC_PHONE_NUMBER_ID", "APMC_WEBHOOK_URL", "APMC"),
]

for _pid_key, _url_key, _name in _routing_pairs:
    _phone_id = os.getenv(_pid_key, "").strip()
    _url      = os.getenv(_url_key, "").strip()
    if _phone_id and _url:
        PHONE_TO_SERVICE[_phone_id] = {"name": _name, "webhook_url": _url}


# ── Background forwarder ──────────────────────────────────────────────────────

async def _forward_to_service(
    service_name: str,
    service_url: str,
    raw_body: bytes,
    headers: dict[str, str],
) -> None:
    """POST the raw webhook body to a downstream service (non-blocking).

    The body is forwarded byte-for-byte (not re-serialized) and the original
    ``X-Hub-Signature-256`` header is propagated, so a downstream service can
    re-validate the Meta signature if it chooses to.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(service_url, content=raw_body, headers=headers)
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "services": {
            info["name"]: info["webhook_url"]
            for info in PHONE_TO_SERVICE.values()
        },
    }


@app.get("/")
async def root() -> PlainTextResponse:
    return PlainTextResponse("WhatsApp Webhook Router — see /health for registered services.")


@app.get("/webhook")
async def webhook_verify(request: Request) -> Response:
    mode      = request.query_params.get("hub.mode")
    token     = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        return PlainTextResponse(challenge or "", status_code=200)

    return Response(status_code=403)


@app.post("/webhook")
async def webhook_receiver(request: Request, background_tasks: BackgroundTasks) -> Response:
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except Exception:
        return Response(status_code=200)

    phone_number_id = get_phone_number_id(payload)
    service = PHONE_TO_SERVICE.get(phone_number_id)

    if service:
        fwd_headers = {
            "Content-Type": "application/json",
            "X-WA-Service": service["name"],
            "X-WA-Phone-Number-Id": phone_number_id,
        }
        signature = request.headers.get("x-hub-signature-256")
        if signature:
            fwd_headers["X-Hub-Signature-256"] = signature

        background_tasks.add_task(
            _forward_to_service,
            service["name"],
            service["webhook_url"],
            raw_body,
            fwd_headers,
        )
    return Response(status_code=200)


def main() -> None:
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)


if __name__ == "__main__":
    main()