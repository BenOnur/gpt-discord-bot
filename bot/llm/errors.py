from __future__ import annotations

from typing import Tuple


class LLMError(Exception):
    """Base error for LLM-related failures."""


class LLMRateLimitError(LLMError):
    pass


class LLMAuthError(LLMError):
    pass


class LLMNotFoundError(LLMError):
    pass


class LLMForbiddenError(LLMError):
    pass


class LLMConnectionError(LLMError):
    pass


class LLMToolError(LLMError):
    pass


def parse_error_message(error: Exception) -> str:
    """
    Map raw exceptions into short, human-readable messages.
    Used for admin notifications and logs.
    """
    s, t = str(error), type(error).__name__
    if "429" in s or t == "RateLimitError":
        return "⚠️ Rate Limited: API provider is temporarily rate-limited. Please retry shortly."
    if "401" in s or "Unauthorized" in s:
        return "❌ Authentication Error: Invalid API key or credentials."
    if "404" in s or t == "NotFound":
        return "❌ Not Found: The requested resource was not found."
    if "403" in s or t == "Forbidden":
        return "❌ Forbidden: You don't have permission to access this resource."
    if "Connection" in t or "ECONNREFUSED" in s or "ETIMEDOUT" in s:
        if "Ollama" in s or "ollama" in s:
            return "❌ Connection Error: Unable to connect to Ollama. Please ensure Ollama is running (ollama serve) and accessible."
        return "❌ Connection Error: Unable to connect to the API provider. Check network and API endpoint."
    return f"❌ {t}: {s.split(chr(10))[0][:100]}"


def format_user_friendly_error(error: Exception) -> str:
    """
    Short, safe error message suitable for end users.
    """
    s, t = str(error), type(error).__name__
    if "429" in s or t == "RateLimitError":
        return "Modelin ücretsiz kullanım kotası doldu. Biraz sonra tekrar dene veya başka model seç."
    if "401" in s or "Unauthorized" in s:
        return "Modelin API anahtarı doğrulanamadı. Yönetici anahtar ayarını kontrol etmeli."
    if "404" in s or t == "NotFound":
        return "Seçilen model bulunamadı. Başka bir model seç."
    if "403" in s or t == "Forbidden":
        return "Bu modele erişim izni yok. Başka bir model seç."
    if "Connection" in t or "ECONNREFUSED" in s or "ETIMEDOUT" in s:
        return "Model servisine bağlanılamadı. Biraz sonra tekrar dene."
    if "413" in s or "token sınırına" in s:
        return "Sohbet isteği modelin ücretsiz token sınırını aşıyor. Daha kısa bir mesajla dene veya başka model seç."
    return "Model yanıt veremedi. Yöneticiye bilgi verildi; biraz sonra tekrar dene."


def error_messages(error: Exception) -> Tuple[str, str]:
    """
    Convenience helper returning (admin_message, user_message).
    """
    return parse_error_message(error), format_user_friendly_error(error)

