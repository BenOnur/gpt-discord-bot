"""
Spontaneous Chat Interjection (Sohbete Salça Olma / Ortaya Laf Atma).
Monitors normal server chat without burning tokens.

Uses local Python heuristics (0 tokens spent while listening):
- Cooldown timer (default 15 minutes between interjections)
- Minimum message count threshold (default 12 user messages)
- Provocative / drama keyword detection (boosts probability & lowers required message count)
- Random chance roll (zero tokens spent if roll fails)
- When triggered, formats recent conversation, calls LLM for a snappy 1-2 sentence comeback,
  and sends it to the channel.
"""
from __future__ import annotations

from collections import deque
import logging
import random
import re
import time
from typing import Any, Callable

import discord

logger = logging.getLogger(__name__)

# Trigger keywords for Turkish Discord arguments, gaming tilt, and banter
DEFAULT_TRIGGER_KEYWORDS = [
    "yalan", "haksız", "saçmalama", "iddia", "kes sesini", "boş yapma",
    "sus", "hadi ordan", "kanka", "reis", "hocam", "valla", "salak",
    "mal", "aptal", "lan", "aq", "amk", "sg", "aynen", "ezik", "kolsuz",
    "ağla", "kudur", "çöp", "bot"
]


class ChannelChatTracker:
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.last_bot_activity = time.time()
        self.user_message_count = 0
        self.recent_messages: deque[dict[str, str]] = deque(maxlen=15)

    def record_user_message(self, author_name: str, content: str) -> None:
        self.user_message_count += 1
        self.recent_messages.append({
            "author": author_name,
            "content": content,
        })

    def record_bot_activity(self) -> None:
        self.last_bot_activity = time.time()
        self.user_message_count = 0


class SpontaneousChatManager:
    def __init__(self):
        self.trackers: dict[int, ChannelChatTracker] = {}

    def get_tracker(self, channel_id: int) -> ChannelChatTracker:
        if channel_id not in self.trackers:
            self.trackers[channel_id] = ChannelChatTracker(channel_id)
        return self.trackers[channel_id]

    def record_bot_activity(self, channel_id: int) -> None:
        self.get_tracker(channel_id).record_bot_activity()

    def should_chime_in(self, message: discord.Message, config: dict[str, Any]) -> tuple[bool, str]:
        """
        Evaluate local zero-token heuristics to decide if the bot should chime in.
        Returns (should_chime, reason).
        """
        cfg = config.get("spontaneous_chat", {})
        if not cfg.get("enabled", True):
            return False, "feature disabled"

        # Check channel whitelist if defined
        allowed_channels = cfg.get("channel_ids") or []
        if allowed_channels and message.channel.id not in allowed_channels:
            return False, "channel not in spontaneous allowed list"

        # Check channel blacklist from permissions
        perms = config.get("permissions", {}).get("channels", {})
        if perms.get("blocked_ids") and message.channel.id in perms["blocked_ids"]:
            return False, "channel blocked"

        # Ignore empty content or bot commands
        raw_text = (message.clean_content or message.content or "").strip()
        if not raw_text or raw_text.startswith(("/", "!", "$", ".", "-")):
            return False, "empty or command message"

        tracker = self.get_tracker(message.channel.id)
        author_name = getattr(message.author, "display_name", message.author.name)
        tracker.record_user_message(author_name, raw_text)

        # Configuration parameters with sensible defaults
        cooldown_sec = cfg.get("cooldown_seconds", 900)  # 15 minutes default
        min_messages = cfg.get("min_user_messages", 12)  # 12 user messages default
        base_probability = cfg.get("probability", 0.15)  # 15% default
        boost_probability = cfg.get("boost_probability", 0.40)  # 40% default

        # 1. Cooldown gate (absolute limit - guarantees zero spam)
        elapsed = time.time() - tracker.last_bot_activity
        if elapsed < cooldown_sec:
            return False, f"cooldown active ({int(elapsed)}s / {cooldown_sec}s)"

        # 2. Drama / Provocation detection
        text_lower = raw_text.lower()
        trigger_keywords = cfg.get("trigger_keywords") or DEFAULT_TRIGGER_KEYWORDS
        has_trigger = any(kw in text_lower for kw in trigger_keywords)

        # Detect ALL CAPS shout/tilt (> 8 letters, > 65% uppercase)
        clean_letters = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşü]", "", raw_text)
        is_caps = len(clean_letters) >= 8 and (sum(1 for c in clean_letters if c.isupper()) / len(clean_letters)) > 0.65

        is_boosted = has_trigger or is_caps

        # 3. Message count threshold (halved if there is active drama/anger)
        effective_min_msgs = max(5, min_messages // 2) if is_boosted else min_messages
        if tracker.user_message_count < effective_min_msgs:
            return False, f"insufficient messages ({tracker.user_message_count}/{effective_min_msgs})"

        # 4. Probability dice roll
        effective_prob = boost_probability if is_boosted else base_probability
        roll = random.random()
        if roll >= effective_prob:
            return False, f"dice roll failed ({roll:.2f} >= {effective_prob:.2f})"

        return True, "triggered (boosted drama/tilt)" if is_boosted else "triggered (natural banter)"


spontaneous_manager = SpontaneousChatManager()


async def maybe_handle_spontaneous(
    message: discord.Message,
    config: dict[str, Any],
    curr_model: str,
    curr_persona: str,
    build_client_fn: Callable[[dict], Any],
    try_load_persona_fn: Callable[[str], str | None],
) -> bool:
    """
    Called on every non-bot message in server channels where the bot was NOT mentioned.
    If heuristics pass, generates a spontaneous comeback and posts it to the channel.
    Returns True if an interjection was sent, False otherwise.
    """
    should_chime, reason = spontaneous_manager.should_chime_in(message, config)
    if not should_chime:
        logger.debug("Spontaneous chat skip in #%s: %s", getattr(message.channel, "name", message.channel.id), reason)
        return False

    logger.info("🔥 Spontaneous chat TRIGGERED in #%s: %s", getattr(message.channel, "name", message.channel.id), reason)

    tracker = spontaneous_manager.get_tracker(message.channel.id)
    recent = list(tracker.recent_messages)
    if not recent:
        return False

    # Format dialogue history
    dialogue_lines = []
    for m in recent[-8:]:
        dialogue_lines.append(f"[{m['author']}]: {m['content']}")
    dialogue_text = "\n".join(dialogue_lines)

    # Resolve system prompt / persona
    persona_body = try_load_persona_fn(curr_persona) or config.get("system_prompt", "")
    system_prompt = (
        persona_body.strip()
        + "\n\n"
        + "ÖZEL GÖREV (SOHBETE SALÇA OLMA):"
        + "\nYukarıdaki Discord muhabbetini arkadan dinledin. Arkadaşların aralarında konuşurken/tartışırken tam zamanında ortaya bombayı bırakmak veya birine laf sokmak istiyorsun."
        + "\n- ASLA VİRGÜL (,) KULLANMA. Virgül tamamen yasaktır."
        + "\n- 'Reis', 'valla', 'hocam', 'kanka' gibi yaşlı ve bayat kelimeleri KESİNLİKLE KULLANMA."
        + "\n- Asla kendini tanıtma veya 'merhaba', 'selam' gibi dolgular yapma."
        + "\n- Doğrudan ortaya veya son konuşan kişiye yönelik 1 veya en fazla 2 kısa cümleyle lafını yapıştır."
    )

    api_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Aşağıdaki konuşmayı dinledin. Ortaya lafını söyle veya birine salla:\n\n{dialogue_text}"
        }
    ]

    # Resolve model and provider
    provider_name, model_name = curr_model.removesuffix(":vision").split("/", 1)
    provider_cfg = config.get("providers", {}).get(provider_name)
    if not provider_cfg:
        logger.warning("Spontaneous chat provider '%s' not found in config", provider_name)
        return False

    client = build_client_fn(provider_cfg)

    try:
        async with message.channel.typing():
            response = await client.chat.completions.create(
                model=model_name,
                messages=api_messages,
                max_tokens=100,
            )
            choice = response.choices[0] if response.choices else None
            if not choice or not choice.message:
                return False

            raw_reply = choice.message.content or ""
            # Strip thinking tags and commas
            reply_text = re.sub(r"<think>.*?</think>", "", raw_reply, flags=re.DOTALL).strip()
            reply_text = reply_text.replace(",", "")

            if not reply_text:
                return False

            # Send as reply to the last triggering message without pinging the user
            await message.reply(reply_text, mention_author=False)
            spontaneous_manager.record_bot_activity(message.channel.id)
            logger.info("Spontaneous comeback sent to #%s: %s", getattr(message.channel, "name", message.channel.id), reply_text)
            return True

    except Exception as exc:
        logger.warning("Spontaneous chat error: %s", exc)
        return False
