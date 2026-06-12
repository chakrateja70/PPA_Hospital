# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhatsApp Flows backend for the Paradip Port Authority (PPA) hospital appointment booking system. Citizens interact with WhatsApp Flow screens to book appointments, look up doctors, check lab reports, and access health guidance. All communication with Meta's platform uses end-to-end encryption (RSA-OAEP + AES-GCM).

## Setup

```powershell
pip install -r requirements.txt
# Generate RSA key pair for encryption
python -m src.key_generator <passphrase>
# Copy .env.example to .env and fill in values
```

## Running

Both servers must be running simultaneously. They are independent processes on different ports.

```powershell
# Flow endpoint (handles encrypted WhatsApp Flow requests from main.py proxy)
uvicorn src.server:app --reload --port 3000

# Webhook router (public-facing; receives Meta webhooks and proxies flow requests)
uvicorn main:app --reload --port 9000
```

## Architecture

Two independent FastAPI servers:

**`main.py` (port 9000)** — Public-facing server with two distinct roles:
- `POST /` — Synchronous proxy: forwards encrypted WhatsApp Flow requests to `src/server.py` (at `FLOW_SERVER_URL`) and returns the encrypted response. Meta's Flow Endpoint URI should point here.
- `POST /webhook` — Async router: reads `phone_number_id` from the Meta payload and forwards the raw body + `X-Hub-Signature-256` to the matching downstream service URL in a background task. Service pairs are configured via `PPA_PHONE_NUMBER_ID`/`PPA_WEBHOOK_URL` env vars.
- `GET /webhook` — Meta webhook verification handshake.

**`src/server.py` (port 3000)** — Flow endpoint (internal, called only by `main.py`):
- `POST /` — Validates HMAC signature, decrypts RSA+AES-GCM payload, calls `get_next_screen()`, encrypts and returns response.
- `POST /webhook` — Handles plain JSON webhook events forwarded by the router: sends the interactive Flow message on text, routes audio to `src/audio_service.py` via BackgroundTask, sends confirmation/video on `nfm_reply` flow completion.

**`src/flow.py`** — All screen routing and business logic. `get_next_screen()` dispatches on `screen` + `action`. Reference data (departments, hospitals, OPD info, time slots) is loaded from `data/*.json` at module import time.

**`src/audio_service.py`** — Audio Guidance pipeline: ElevenLabs STT → Groq LLM (direct call, no RAG) → translation → ElevenLabs TTS → WhatsApp voice reply. Every stage falls back to a text reply. Heavy SDKs are imported lazily inside the functions that use them.

**`src/encryption.py`** — Stateless helpers: `decrypt_request()` and `encrypt_response()`. No business logic.

**`src/key_generator.py`** — Generates an RSA key pair and prints the PEM-encoded private/public keys. Run once during setup; upload the public key to Meta Business Manager.

**`flow.json`** — WhatsApp Flow UI definition (screens, form fields, routing model). Any change here requires re-publishing the Flow in Meta Business Manager. Screen IDs and data key names must stay consistent with `src/flow.py`.

## Request Lifecycle

```
User taps "Choose Service" CTA in WhatsApp
  → Meta sends POST / to main.py (INIT action, encrypted)
  → main.py proxies to src/server.py POST /
  → src/server.py decrypts → get_next_screen() returns SERVICES screen
  → encrypted response back to Meta → WhatsApp renders screen

User taps a service item (e.g. Appointment Booking)
  → Meta sends POST / to main.py (data_exchange, screen=SERVICES)
  → same proxy chain → get_next_screen() returns APPOINTMENT screen data

On flow completion (nfm_reply webhook event):
  → Meta sends POST /webhook to main.py
  → main.py routes to src/server.py POST /webhook (via PPA_WEBHOOK_URL)
  → src/server.py sends confirmation message / video via Graph API
```

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `APP_SECRET` | HMAC key for `x-hub-signature-256` validation in `src/server.py` |
| `PRIVATE_KEY` | PEM RSA private key (newlines as `\n`) for decrypting flow requests |
| `PASSPHRASE` | Passphrase for the encrypted private key |
| `GRAPH_API_TOKEN` | Meta Graph API bearer token for sending messages |
| `FLOW_ID` | WhatsApp Flow ID registered in Meta Business |
| `flow_token` | Flow token passed in the interactive message action (default: `flows-builder-token`) |
| `FLOW_SERVER_URL` | URL of `src/server.py` as seen from `main.py` (default: `http://localhost:3000`) |
| `ROUTER_PORT` | Port for `main.py` (default: `9000`) |
| `PORT` | Port for `src/server.py` (default: `3000`) |
| `WEBHOOK_VERIFY_TOKEN` | Token for Meta webhook verification handshake |
| `PPA_PHONE_NUMBER_ID` / `PPA_WEBHOOK_URL` | Phone-to-service routing pair for PPA |
| `PPA_ALLOWED_NUMBERS` | Comma-separated sender allowlist for rollout gating; empty = allow all |
| `ELEVENLABS_API_KEY` | ElevenLabs key for Audio Guidance STT + TTS |
| `ELEVENLABS_VOICE_ID` / `ELEVENLABS_MODEL_ID` / `ELEVENLABS_STT_MODEL` | ElevenLabs voice/model config (have defaults) |
| `GROQ_API_KEY` / `GROQ_LLM_MODEL` | Groq key + model for the Audio Guidance LLM call |

## Screen Flow (`src/flow.py`)

```
SERVICES → APPOINTMENT → DETAILS → SUMMARY → TERMS   (appointment booking)
SERVICES → DOCTOR_OPD → DOCTOR_OPD_RESULT             (department only; hospital fixed to PPA)
SERVICES → LAB_REPORTS → LAB_REPORT_VIEW
SERVICES → AI_ASSISTANCE                               (completes flow; answer sent via nfm_reply)
SERVICES → HOSPITAL_INFO → HOSPITAL_DETAIL            (note: service key is HOSPITAL_INFO; screen id is HOSPITAL_DETAIL)
SERVICES → EMERGENCY_VIDEOS                           (completes flow; video sent via nfm_reply)
SERVICES → AUDIO_GUIDANCE                             (completes flow; voice Q&A happens over plain WhatsApp messages)
```

All non-appointment paths complete with a `SUCCESS` screen that carries data back via `extension_message_response.params`, which Meta delivers as an `nfm_reply` webhook event.

## Data Layer

`data/departments.json`, `data/hospitals.json`, `data/opd_info.json`, `data/time_slots.json` — all reference data, no database. Lab report content and AI responses in the Flow are mock/templated data.

## Key Invariants

- `PRIVATE_KEY` must be set or `src/server.py` raises `RuntimeError` on every flow request.
- Both servers must be running. `main.py POST /` returns 503 if `src/server.py` is unreachable.
- Appointment dates exclude past dates; time slots for today exclude slots within 1 hour.
- `flow.json` screen IDs and data keys are coupled to handler names in `src/flow.py`; renaming requires updating both and re-publishing the Flow.
- `src/server.py` ignores webhook events whose `phone_number_id` does not match `PPA_PHONE_NUMBER_ID`.
