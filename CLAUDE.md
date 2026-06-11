# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhatsApp Flows backend for the Telangana government hospital appointment booking system. Citizens interact with WhatsApp Flow screens to book appointments, look up doctors, check lab reports, and access health guidance. All communication with Meta's platform uses end-to-end encryption (RSA-OAEP + AES-GCM).

## Setup

```powershell
pip install -r requirements.txt
# Generate RSA key pair for encryption
python -m src.key_generator <passphrase>
# Copy .env.example to .env and fill in values
```

## Running

```powershell
# Flow endpoint (handles encrypted WhatsApp Flow requests)
uvicorn src.server:app --reload --port 3000

# Webhook router (receives Meta webhooks, routes to downstream services)
uvicorn main:app --reload --port 9000
```

## Architecture

Two independent FastAPI servers:

**`main.py` (port 9000)** — Webhook router. Reads `phone_number_id` from the Meta payload and forwards the raw body to the matching downstream service URL, propagating the `X-Hub-Signature-256` header plus `X-WA-Service` / `X-WA-Phone-Number-Id` so downstream can identify and (optionally) re-validate the request. Service pairs are configured via environment variables (`THS_PHONE_NUMBER_ID` / `THS_WEBHOOK_URL`, etc.). Note: the router does not currently *enforce* the HMAC signature on `POST /webhook` — see `is_request_signature_valid` in `src/server.py` for the validation helper to reuse if you want to enforce it at the edge.

**`src/server.py` (port 3000)** — Flow endpoint. Receives encrypted flow requests from the router, decrypts with RSA private key + AES-GCM session key, delegates to `src/flow.py` for screen logic, and returns an encrypted response. Also handles `nfm_reply` webhook events (flow completions) to send confirmation messages and emergency videos via the Graph API, and routes incoming **audio** messages to the Audio Guidance pipeline (`src/audio_service.py`) via a `BackgroundTask`.

**`src/flow.py`** — All screen routing and business logic. `get_next_screen()` is the main entry point, dispatching on `screen` + `action` to return the next screen data. Static reference data (departments, districts, hospitals, time slots, OPD info) is loaded from `data/*.json`.

**`src/audio_service.py`** — Audio Guidance service. When a user picks "Audio Guidance" and confirms in the Flow, the webhook sends a "send your query through audio" prompt; the user's voice note is then transcribed (ElevenLabs STT), answered with a **direct LLM call** (Groq — no RAG), translated back to the caller's language, converted to speech (ElevenLabs TTS), and sent back as a WhatsApp voice message. Every stage falls back to a text reply on failure. Heavy SDKs are imported lazily, so the module imports even when those packages aren't installed.

**`src/encryption.py`** — Standalone helpers: decrypt incoming request body, encrypt outgoing response. No business logic.

**`flow.json`** — WhatsApp Flow UI definition (screens, form fields, data structures). Changes here must stay consistent with the screen names and data keys used in `src/flow.py`.

## Request Lifecycle

```
Meta Cloud → POST /webhook (main.py)
  → validates x-hub-signature-256
  → forwards to src/server.py based on phone_number_id

Meta Cloud → POST / (src/server.py)
  → validates signature
  → decrypts payload (src/encryption.py)
  → get_next_screen() (src/flow.py)
  → encrypts response
  → returns to Meta → WhatsApp user sees next screen

On flow completion (nfm_reply):
  → src/server.py sends confirmation message + optional video via Graph API
```

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `APP_SECRET` | HMAC key for request signature validation |
| `PRIVATE_KEY` | RSA private key for decrypting flow requests |
| `PASSPHRASE` | Passphrase for the encrypted private key |
| `GRAPH_API_TOKEN` | Meta Graph API token for sending messages |
| `FLOW_ID` | WhatsApp Flow ID registered in Meta Business |
| `THS_PHONE_NUMBER_ID` / `THS_WEBHOOK_URL` | Phone-to-service routing pair |
| `WEBHOOK_VERIFY_TOKEN` | Token for Meta webhook verification handshake |
| `ELEVENLABS_API_KEY` | ElevenLabs key for Audio Guidance speech-to-text + text-to-speech |
| `ELEVENLABS_VOICE_ID` / `ELEVENLABS_MODEL_ID` / `ELEVENLABS_STT_MODEL` | Voice, TTS model, and STT model for Audio Guidance (have defaults) |
| `GROQ_API_KEY` / `GROQ_LLM_MODEL` | Groq key + model for the Audio Guidance direct LLM call |

## Screen Names (`src/flow.py`)

`SERVICES` → `APPOINTMENT` → `DETAILS` → `SUMMARY` → `TERMS` (booking path)  
`SERVICES` → `DOCTOR_OPD` / `LAB_REPORTS` / `AI_ASSISTANCE` / `HOSPITAL_INFO` / `EMERGENCY_VIDEOS` / `AUDIO_GUIDANCE`

`AUDIO_GUIDANCE` is a confirm screen that completes the Flow with `service: "audio_guidance"`; the voice Q&A itself happens over plain WhatsApp messages afterwards (see `src/audio_service.py`), not inside the Flow.

## Data Layer

All reference data is in `data/*.json` — no database. The data is stateless and loaded per-request (or cached in memory). Lab reports and AI responses are templated mock data, not connected to real systems.

## Constraints

- `src/server.py` replies only to events whose `phone_number_id` matches `THS_PHONE_NUMBER_ID`, and (during rollout) only to senders in `THS_ALLOWED_NUMBERS` (comma-separated env var; set it empty to allow all senders).
- Appointment date filtering removes past dates; time slot filtering removes past slots for the current day.
- `flow.json` screen/field names must match the keys expected in `src/flow.py`; renaming either requires updating both.
