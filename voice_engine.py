import io
import logging
from openai import OpenAI
import config

logger = logging.getLogger(__name__)

# Default Cedar Voice & TTS model
DEFAULT_VOICE = "cedar"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
FALLBACK_TTS_MODEL = "tts-1"
FALLBACK_VOICE = "onyx"

def get_openai_client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=config.OPENAI_API_KEY)

def transcribe_audio(audio_bytes: bytes, filename: str = "voicenote.ogg") -> str:
    """
    Transcribes audio bytes (e.g. from Telegram voice note or microphone)
    using OpenAI Whisper-1.
    """
    if not audio_bytes:
        return ""
        
    client = get_openai_client()
    try:
        # Wrap bytes into an in-memory binary file tuple for the OpenAI SDK
        file_tuple = (filename, io.BytesIO(audio_bytes), "audio/ogg")
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=file_tuple
        )
        text = transcription.text.strip()
        logger.info(f"Transcribed audio ({len(audio_bytes)} bytes): '{text}'")
        return text
    except Exception as e:
        logger.error(f"Whisper audio transcription failed: {e}", exc_info=True)
        return ""

def generate_speech(text: str, voice: str = DEFAULT_VOICE, model: str = DEFAULT_TTS_MODEL, response_format: str = "mp3") -> bytes:
    """
    Generates spoken audio from text using OpenAI's voice engine.
    Default voice is 'cedar' using 'gpt-4o-mini-tts'.
    """
    clean_text = str(text).strip()
    if not clean_text:
        return b""

    # Truncate to reasonable spoken length for Telegram audio note (e.g. max 1200 characters)
    if len(clean_text) > 1200:
        clean_text = clean_text[:1200] + "..."

    client = get_openai_client()
    
    # Try primary voice and model (e.g. Cedar)
    try:
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            input=clean_text,
            response_format=response_format
        )
        return response.content
    except Exception as e:
        logger.warning(f"TTS with model='{model}' voice='{voice}' failed ({e}). Attempting fallback to '{FALLBACK_TTS_MODEL}' with '{FALLBACK_VOICE}'...")
        try:
            fallback_resp = client.audio.speech.create(
                model=FALLBACK_TTS_MODEL,
                voice=FALLBACK_VOICE,
                input=clean_text,
                response_format=response_format
            )
            return fallback_resp.content
        except Exception as e_fall:
            logger.error(f"Fallback TTS failed: {e_fall}", exc_info=True)
            return b""
