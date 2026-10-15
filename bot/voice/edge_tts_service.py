"""
Zero-configuration Edge-TTS service for Turkish speech generation.
Uses Microsoft Edge TTS endpoints without requiring any Azure API keys or subscriptions.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "tr-TR-AhmetNeural"
FEMALE_VOICE = "tr-TR-EmelNeural"


async def synthesize_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
    retries: int = 3,
    output_path: str | None = None,
) -> str:
    """
    Synthesize text to an MP3 file using Edge-TTS with retry logic.
    
    Args:
        text: Text to speak
        voice: Turkish voice name (defaults to tr-TR-AhmetNeural)
        retries: Number of connection retries on temporary error
        output_path: Target MP3 path. If None, creates a named temp file.
        
    Returns:
        Absolute path to the created audio file.
    """
    import edge_tts

    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Cannot synthesize empty text")

    if not output_path:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            output_path = tf.name

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            communicate = edge_tts.Communicate(clean_text, voice)
            audio_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes += chunk["data"]

            if not audio_bytes:
                raise RuntimeError("Edge-TTS returned zero audio bytes")

            with open(output_path, "wb") as f:
                f.write(audio_bytes)

            logger.info("Edge-TTS synthesized %d bytes to %s", len(audio_bytes), output_path)
            return output_path
        except Exception as e:
            last_err = e
            logger.warning("Edge-TTS attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                await asyncio.sleep(1.2)

    raise RuntimeError(f"Edge-TTS failed after {retries} attempts: {last_err}")
