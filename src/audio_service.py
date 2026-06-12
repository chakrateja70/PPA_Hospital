"""Audio Guidance service.

Pipeline for the "Audio Guidance" flow service: a citizen sends a WhatsApp
voice note, we transcribe it (ElevenLabs speech-to-text), answer the query with
a *direct* LLM call (Groq — no RAG / vector store), translate the answer back
into the caller's language, convert it to speech (ElevenLabs text-to-speech) and
send it back as a WhatsApp voice message.

The whole pipeline is synchronous and meant to be run from a FastAPI
``BackgroundTask`` (so the webhook can return 200 to Meta immediately). Every
stage falls back to a plain text reply if audio handling fails, so the user
always gets *an* answer.

The heavy third-party SDKs (elevenlabs, groq, deep_translator) are imported
lazily inside the functions that use them, so importing this module never fails
even if those packages are not installed — they are only needed when an audio
message is actually processed.

Mirrors the logic in the apmc-chatbot project
(``src/services/whatsapp_service.py`` + ``src/utils/whatsapp_helper.py``) but
swaps the RAG lookup for a single context-grounded LLM call.
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from typing import Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
GRAPH_API_TOKEN     = os.getenv("GRAPH_API_TOKEN", "")
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel – free-tier premade voice
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
GROQ_LLM_MODEL      = os.getenv("GROQ_LLM_MODEL", "llama-3.1-8b-instant")

GRAPH_VERSION = "v22.0"
HTTP_TIMEOUT  = 30
MAX_TTS_CHARS = 550  # keep ElevenLabs TTS within a sensible quota / length

# ── LLM Prompts ──────────────────────────────────────────────────────────────

# Used by AI Assistance (text display inside the WhatsApp Flow screen).
# Produces a numbered, emoji-enhanced plain-text response readable on phone.
AI_TEXT_PROMPT = (
    "You are a helpful health assistant for a government hospital system "
    "(Paradip Port Authority). A patient has submitted a health question "
    "through a WhatsApp chatbot. Give a clear, safe, and actionable response.\n\n"
    "Formatting rules — follow exactly:\n"
    "- Start directly with the answer. Do not use openers like 'Here is...' or 'Sure!'.\n"
    "- Use numbered points (e.g.  1. Rest and stay hydrated).\n"
    "- Plain text only — no markdown, no ** bold **, no ## headings.\n"
    "- Use relevant emojis sparingly (💧 for hydration, 🌡️ for fever, 💊 for medicine).\n"
    "- Keep the entire response under 500 characters.\n"
    "- The last line must always be: ⚠️ Consult a doctor for proper diagnosis.\n"
    "- If it sounds like an emergency, add: 🚨 Call 108 immediately.\n"
    "- Never prescribe specific medicines, dosages, or make a diagnosis.\n\n"
    "Patient's question: {question}\n\n"
    "Response:"
)

# Used by Audio Guidance (voice reply — must be conversational, no lists/emojis).
HEALTH_CONTEXT_PROMPT = (
    "You are a warm, helpful voice health assistant for a government hospital "
    "system (Paradip Port Authority). A citizen has asked the following "
    "question in a voice message. Answer it directly and helpfully.\n\n"
    "Guidelines:\n"
    "- Keep the answer short and conversational — it will be read aloud as "
    "audio (aim for 3-5 short sentences, under ~80 words).\n"
    "- Use simple, everyday language. Avoid medical jargon and avoid lists, "
    "headings, emojis or special formatting.\n"
    "- Give safe, practical, general guidance.\n"
    "- If the symptoms sound serious or like an emergency, tell them to seek "
    "immediate medical help or call 108.\n"
    "- Do not prescribe specific medicines or dosages, and do not give a firm "
    "diagnosis.\n"
    "- When relevant, gently remind them to consult a doctor for proper "
    "advice.\n\n"
    "Citizen's question: {question}\n\n"
    "Answer:"
)

# ElevenLabs returns ISO 639-3 (3-letter) language codes; GoogleTranslator wants
# ISO 639-1 (2-letter). Same mapping used by apmc-chatbot.
_LANG_MAP = {
    'afr': 'af', 'ara': 'ar', 'hye': 'hy', 'asm': 'as', 'aze': 'az',
    'bel': 'be', 'ben': 'bn', 'bos': 'bs', 'bul': 'bg', 'cat': 'ca',
    'ceb': 'ceb', 'nya': 'ny', 'hrv': 'hr', 'ces': 'cs', 'dan': 'da',
    'nld': 'nl', 'eng': 'en', 'est': 'et', 'fil': 'tl', 'fin': 'fi',
    'fra': 'fr', 'glg': 'gl', 'kat': 'ka', 'deu': 'de', 'ell': 'el',
    'guj': 'gu', 'hau': 'ha', 'heb': 'he', 'hin': 'hi', 'hun': 'hu',
    'isl': 'is', 'ind': 'id', 'gle': 'ga', 'ita': 'it', 'jpn': 'ja',
    'jav': 'jv', 'kan': 'kn', 'kaz': 'kk', 'kir': 'ky', 'kor': 'ko',
    'lav': 'lv', 'lin': 'ln', 'lit': 'lt', 'ltz': 'lb', 'mkd': 'mk',
    'msa': 'ms', 'mal': 'ml', 'cmn': 'zh-CN', 'mar': 'mr', 'nep': 'ne',
    'nor': 'no', 'pus': 'ps', 'fas': 'fa', 'pol': 'pl', 'por': 'pt',
    'pan': 'pa', 'ron': 'ro', 'rus': 'ru', 'srp': 'sr', 'snd': 'sd',
    'slk': 'sk', 'slv': 'sl', 'som': 'so', 'spa': 'es', 'swa': 'sw',
    'swe': 'sv', 'tam': 'ta', 'tel': 'te', 'tha': 'th', 'tur': 'tr',
    'ukr': 'uk', 'urd': 'ur', 'vie': 'vi', 'cym': 'cy',
}

# The prompt the user receives on WhatsApp after confirming the service.
AUDIO_PROMPT_MESSAGE = (
    "🎤 *Audio Guidance*\n\n"
    "Send your query through audio in any language, and I'll reply with an "
    "audio answer.\n\n"
    "Just record and send a voice message now."
)


# ── Lazy SDK singletons ──────────────────────────────────────────────────────
_eleven = None
_groq = None


def _elevenlabs():
    global _eleven
    if _eleven is None:
        from elevenlabs.client import ElevenLabs
        _eleven = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    return _eleven


def _groq_client():
    global _groq
    if _groq is None:
        from groq import Groq
        _groq = Groq(api_key=GROQ_API_KEY)
    return _groq


def _is_english(language_code: Optional[str]) -> bool:
    return (language_code or "").lower() in ("", "en", "eng", "english")


# ── WhatsApp Graph helpers ───────────────────────────────────────────────────

def send_text_message(to: str, text: str, phone_number_id: str) -> bool:
    """Send a plain WhatsApp text message (used for prompts and fallbacks)."""
    return _send(phone_number_id, {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    })


def send_audio_message(to: str, media_id: str, phone_number_id: str) -> bool:
    """Send a WhatsApp voice message from an uploaded media id."""
    return _send(phone_number_id, {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id},
    })


def _send(phone_number_id: str, payload: dict) -> bool:
    try:
        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {GRAPH_API_TOKEN}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[audio] send failed: {exc}")
        return False


# ── Pipeline stages ──────────────────────────────────────────────────────────

def transcribe_audio(media_id: str) -> Optional[Tuple[str, str]]:
    """Download a WhatsApp audio media and transcribe it with ElevenLabs.

    Returns ``(transcribed_text, language_code)`` or ``None`` on failure.
    """
    try:
        headers = {"Authorization": f"Bearer {GRAPH_API_TOKEN}"}
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            meta = client.get(
                f"https://graph.facebook.com/{GRAPH_VERSION}/{media_id}",
                headers=headers,
            )
            meta.raise_for_status()
            audio_url = meta.json().get("url")
            if not audio_url:
                return None

            audio_resp = client.get(audio_url, headers=headers)
            audio_resp.raise_for_status()
            content = audio_resp.content

        audio_file = BytesIO(content)
        audio_file.name = "voice.ogg"

        transcription = _elevenlabs().speech_to_text.convert(
            file=audio_file,
            model_id=ELEVENLABS_STT_MODEL,
            tag_audio_events=True,
            diarize=True,
            language_code=None,
        )
        if transcription.text and transcription.text.strip():
            return transcription.text, transcription.language_code
        return None
    except Exception as exc:
        print(f"[audio] transcription failed: {exc}")
        return None


def translate(text: str, target: str) -> str:
    """Translate ``text`` to ``target`` (ISO 639-1). Returns original on failure."""
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="auto", target=target).translate(text)
    except Exception:
        return text


def generate_answer(question: str, context: str = HEALTH_CONTEXT_PROMPT) -> Optional[str]:
    """Answer ``question`` with a single direct LLM call (no RAG).

    ``context`` is the prompt template framing the assistant; it must contain a
    ``{question}`` placeholder.
    """
    try:
        prompt = context.format(question=question)
        completion = _groq_client().chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=GROQ_LLM_MODEL,
            temperature=0.3,
            max_tokens=400,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[audio] LLM call failed: {exc}")
        return None


def text_to_speech(text: str) -> Optional[bytes]:
    """Convert ``text`` to MP3 audio bytes with ElevenLabs. ``None`` on failure."""
    try:
        audio_generator = _elevenlabs().text_to_speech.convert(
            text=text,
            voice_id=ELEVENLABS_VOICE_ID,
            model_id=ELEVENLABS_MODEL_ID,
        )
        return b"".join(audio_generator)
    except Exception as exc:
        print(f"[audio] text-to-speech failed: {exc}")
        return None


def upload_audio(audio_bytes: bytes, phone_number_id: str) -> Optional[str]:
    """Upload audio bytes to WhatsApp and return the resulting media id."""
    try:
        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id}/media"
        files = {"file": ("audio.mp3", audio_bytes, "audio/mpeg")}
        data = {"messaging_product": "whatsapp"}
        headers = {"Authorization": f"Bearer {GRAPH_API_TOKEN}"}
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, files=files, data=data, headers=headers)
            resp.raise_for_status()
        return resp.json().get("id")
    except Exception as exc:
        print(f"[audio] audio upload failed: {exc}")
        return None


def _prepare_for_tts(answer: str) -> str:
    """Strip stray formatting/metadata and truncate to a TTS-friendly length."""
    cleaned = answer
    metadata_patterns = [
        r'DOCUMENT\s+\d+:',
        r'Title:\s*[^\n]*',
        r'Category:\s*[^\n]*',
        r'Relevance Score:\s*[\d.]+',
        r'Content:\s*',
        r'---+',
    ]
    for pattern in metadata_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r' {2,}', ' ', cleaned).strip()

    if len(cleaned) <= MAX_TTS_CHARS:
        return cleaned

    truncation_suffix = "... For more details, please send a text message."
    available_length = MAX_TTS_CHARS - len(truncation_suffix)
    if available_length <= 0:
        return cleaned[:MAX_TTS_CHARS]

    candidate = cleaned[:available_length]
    sentence_break = max(candidate.rfind(ch) for ch in (".", "!", "?"))
    if sentence_break != -1:
        cut_pos = sentence_break + 1
    else:
        last_space = candidate.rfind(" ")
        cut_pos = last_space if last_space != -1 else available_length
    return candidate[:cut_pos].rstrip() + truncation_suffix


# ── Orchestrator ─────────────────────────────────────────────────────────────

def handle_audio_query(media_id: str, sender: str, phone_number_id: str) -> None:
    """Full audio-guidance pipeline. Safe to run in a background thread.

    transcribe → translate to English → direct LLM answer → translate back →
    text-to-speech → upload → send voice message (text fallback at each stage).
    """
    # 1. Speech-to-text
    result = transcribe_audio(media_id)
    if not result:
        send_text_message(
            sender,
            "Sorry, I couldn't understand the audio. Please try again or send a text message.",
            phone_number_id,
        )
        return

    transcribed_text, language_code = result
    print(f"[audio] transcribed ({language_code}): {transcribed_text}")

    # 2. Translate the question to English for the LLM
    if _is_english(language_code):
        english_query = transcribed_text
    else:
        english_query = translate(transcribed_text, "en")
        print(f"[audio] translated to English: {english_query}")

    # 3. Direct LLM answer (no RAG)
    answer = generate_answer(english_query)
    if not answer:
        send_text_message(
            sender,
            "Sorry, I couldn't process your request right now. Please try again.",
            phone_number_id,
        )
        return

    # 4. Translate the answer back into the caller's language
    if _is_english(language_code):
        localized_answer = answer
    else:
        target_lang = _LANG_MAP.get(language_code, (language_code or "")[:2] or "en")
        localized_answer = translate(answer, target_lang)
        print(f"[audio] translated answer to {language_code} ({target_lang})")

    # 5. Text-to-speech (fall back to text on failure)
    speech_text = _prepare_for_tts(localized_answer)
    audio_bytes = text_to_speech(speech_text)
    if not audio_bytes:
        send_text_message(sender, localized_answer, phone_number_id)
        return

    # 6. Upload + send the voice reply (fall back to text on failure)
    audio_media_id = upload_audio(audio_bytes, phone_number_id)
    if not audio_media_id or not send_audio_message(sender, audio_media_id, phone_number_id):
        send_text_message(sender, localized_answer, phone_number_id)
        return

    print(f"[audio] voice reply sent to {sender}")
