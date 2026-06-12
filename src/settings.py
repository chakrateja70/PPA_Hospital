from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv(override=True)


class Settings:
    """Application settings loaded from environment variables."""

    def __init__(self):
        # Flow endpoint encryption
        self.APP_SECRET: str = self._get_required("APP_SECRET")
        self.PRIVATE_KEY: str = (os.getenv("PRIVATE_KEY") or "").replace("\\n", "\n") or ""
        self.PASSPHRASE: str = os.getenv("PASSPHRASE", "")
        self.PUBLIC_KEY: str = os.getenv("PUBLIC_KEY", "")

        # Meta Graph API
        self.GRAPH_API_TOKEN: str = self._get_required("GRAPH_API_TOKEN")
        self.FLOW_ID: str = os.getenv("FLOW_ID", "")
        self.FLOW_TOKEN: str = os.getenv("flow_token", "flows-builder-token")

        # PPA Hospital location (sent as a WhatsApp location message)
        self.PPA_HOSPITAL_LATITUDE: str = "20.2664146"
        self.PPA_HOSPITAL_LONGITUDE: str = "86.6609972"
        self.PPA_HOSPITAL_LOCATION_NAME: str = "Paradip Port Authority Hospital"

        # Routing: phone_number_id → downstream webhook URL
        # Add more pairs here as new services are onboarded.
        self.PPA_PHONE_NUMBER_ID: str = os.getenv("PPA_PHONE_NUMBER_ID", "").strip()
        self.PPA_ALLOWED_NUMBERS: set[str] = {
            n.strip()
            for n in os.getenv("PPA_ALLOWED_NUMBERS", "").split(",")
            if n.strip()
        }

        # Audio Guidance — ElevenLabs
        self.ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
        self.ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "jUjRbhZWoMK4aDciW36V")
        self.ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_v3")
        self.ELEVENLABS_STT_MODEL: str = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")

        # Audio Guidance — Groq
        self.GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
        self.GROQ_LLM_MODEL: str = os.getenv("GROQ_LLM_MODEL", "llama-3.1-8b-instant")

    @staticmethod
    def _get_required(key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise ValueError(
                f"{key} not found in environment variables. Please check your .env file."
            )
        return value


xsettings = Settings()
