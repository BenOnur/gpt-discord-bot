"""
Robust Speech Synthesis Service for Turkish Voice Output.
Primary: Microsoft Edge-TTS (tr-TR-AhmetNeural, high-quality neural voice).
Fallback: Google Translate TTS (gTTS, zero-filter, speaks any Turkish slang/profanity without blocking).
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
    retries: int = 2,
    output_path: str | None = None,
) -> str:
    """
    Synthesize text to an MP3 file.
    Tries Edge-TTS (AhmetNeural) first. If Microsoft blocks the text due to profanity
    or safety filters (NoAudioReceived / 400), automatically falls back to gTTS (unfiltered Turkish).
    
    Args:
        text: Text to speak
        voice: Turkish voice name (defaults to tr-TR-AhmetNeural)
        retries: Number of connection retries for Edge-TTS
        output_path: Target MP3 path. If None, creates a named temp file.
        
    Returns:
        Absolute path to the created audio file.
    """
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Cannot synthesize empty text")

    if not output_path:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            output_path = tf.name

    # 1. Try Edge-TTS first (natural neural voice)
    edge_success = False
    for attempt in range(1, retries + 1):
        try:
            import edge_tts
            communicate = edge_tts.Communicate(clean_text, voice)
            audio_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes += chunk["data"]

            if audio_bytes and len(audio_bytes) > 500:
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)
                logger.info("Edge-TTS (%s) synthesized %d bytes to %s", voice, len(audio_bytes), output_path)
                edge_success = True
                return output_path
            else:
                logger.warning("Edge-TTS returned no audio (likely profanity filter), falling back to gTTS")
                break
        except Exception as e:
            logger.warning("Edge-TTS attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                await asyncio.sleep(0.8)

    # 2. Fallback to gTTS (unfiltered Turkish voice - handles swear words and raw slang)
    logger.info("Attempting gTTS fallback for text: %s", clean_text[:60])
    try:
        from gtts import gTTS

        def _generate_gtts():
            tts = gTTS(text=clean_text, lang="tr")
            tts.save(output_path)

        await asyncio.to_thread(_generate_gtts)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            logger.info("gTTS fallback succeeded, file size: %d bytes", os.path.getsize(output_path))
            return output_path
        else:
            raise RuntimeError("gTTS output file is empty or missing")
    except Exception as gtts_err:
        logger.error("gTTS fallback failed: %s", gtts_err)
        raise RuntimeError(f"All speech synthesis providers failed (Edge-TTS and gTTS): {gtts_err}")
