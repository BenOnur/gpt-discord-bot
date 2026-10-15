"""
Robust Speech Synthesis Service for Turkish Voice Output.
Uses Microsoft Edge-TTS (tr-TR-AhmetNeural, natural high-quality Turkish male neural voice).
Includes multi-attempt cluster reconnects to handle Microsoft edge node flapping.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "tr-TR-AhmetNeural"
FEMALE_VOICE = "tr-TR-EmelNeural"


async def synthesize_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
    retries: int = 6,
    output_path: str | None = None,
) -> str:
    """
    Synthesize text to an MP3 file using Microsoft Edge-TTS male neural voice (AhmetNeural).
    Microsoft's Edge Read Aloud endpoint occasionally load-balances to nodes that return
    empty audio streams (NoAudioReceived). This function retries across fresh websocket
    connections to guarantee hitting an active synthesis cluster.

    Args:
        text: Text to speak
        voice: Turkish voice name (defaults to tr-TR-AhmetNeural)
        retries: Number of connection retries for Edge-TTS (default 6)
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

    last_error = None
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
                logger.info(
                    "Edge-TTS (%s) synthesized %d bytes on attempt %d/%d to %s",
                    voice,
                    len(audio_bytes),
                    attempt,
                    retries,
                    output_path,
                )
                return output_path
            else:
                logger.warning(
                    "Edge-TTS attempt %d/%d returned empty audio for voice '%s', retrying...",
                    attempt,
                    retries,
                    voice,
                )
        except Exception as e:
            last_error = e
            logger.warning(
                "Edge-TTS attempt %d/%d failed with exception (%s), retrying...",
                attempt,
                retries,
                e,
            )

        if attempt < retries:
            await asyncio.sleep(0.25)

    raise RuntimeError(
        f"Microsoft Edge-TTS ({voice}) {retries} denemenin ardından ses üretemedi: {last_error}"
    )


