import logging
from app.config import GROQ_API_KEY, GROQ_MODEL, GROQ_TIMEOUT

logger = logging.getLogger("roche_pricing.llm_client")

_GROQ_CLIENT = None

def get_groq_client():
    """
    Returns a persistent, thread-safe Groq client singleton.
    Reuses connection pools across requests to prevent socket churn
    and avoid WinError 10054 (WSAECONNRESET) caused by orphaned client cleanup.
    """
    global _GROQ_CLIENT
    if not GROQ_API_KEY:
        return None
    
    if _GROQ_CLIENT is not None:
        return _GROQ_CLIENT
    
    try:
        from groq import Groq
        _GROQ_CLIENT = Groq(
            api_key=GROQ_API_KEY,
            timeout=GROQ_TIMEOUT,
            max_retries=2
        )
        return _GROQ_CLIENT
    except Exception as exc:
        logger.warning("Failed to initialize persistent Groq client: %s", exc)
        return None

def execute_groq_completion(messages: list[dict], model: str = None, temperature: float = 0.0) -> str | None:
    """
    Executes a Groq chat completion with comprehensive timeout and connection error handling.
    Catches connection resets (WinError 10054), connection errors, timeouts, and rate limits,
    gracefully returning None to trigger deterministic fallbacks.
    """
    client = get_groq_client()
    if not client:
        return None

    use_model = model or GROQ_MODEL

    try:
        from groq import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
    except ImportError:
        APIConnectionError = APITimeoutError = RateLimitError = APIStatusError = Exception

    try:
        response = client.chat.completions.create(
            model=use_model,
            temperature=temperature,
            messages=messages
        )
        return response.choices[0].message.content.strip()
    except (ConnectionResetError, APIConnectionError) as conn_err:
        logger.warning(
            "Groq API connection reset / closed by remote host (WinError 10054 / APIConnectionError): %s. "
            "Falling back to deterministic engine.", conn_err
        )
        return None
    except APITimeoutError as timeout_err:
        logger.warning(
            "Groq API request timed out after %.1fs: %s. Falling back to deterministic engine.",
            GROQ_TIMEOUT, timeout_err
        )
        return None
    except RateLimitError as rl_err:
        logger.warning("Groq API rate limit reached: %s. Falling back to deterministic engine.", rl_err)
        return None
    except APIStatusError as status_err:
        logger.warning("Groq API returned HTTP error %s: %s. Falling back to deterministic engine.", status_err.status_code, status_err)
        return None
    except Exception as exc:
        logger.warning("Unexpected error during Groq completion: %s. Falling back to deterministic engine.", exc)
        return None

