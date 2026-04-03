import os
import json
import asyncio
import logging
from openai import (
    AsyncOpenAI,
    AsyncAzureOpenAI,
    APIError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
    APIConnectionError,
)
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────
MAX_CONCURRENT = int(os.getenv("AI_MAX_CONCURRENT", "10"))
MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "3"))
RETRY_BASE_DELAY = float(os.getenv("AI_RETRY_BASE_DELAY", "2.0"))

# ── Concurrency queue (semaphore) ──────────────────────────────────────────
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# ── Queue stats ────────────────────────────────────────────────────────────
_queue_waiting = 0
_queue_processing = 0
_queue_completed = 0
_queue_failed = 0

def get_queue_stats() -> dict:
    return {
        "max_concurrent": MAX_CONCURRENT,
        "waiting": _queue_waiting,
        "processing": _queue_processing,
        "completed": _queue_completed,
        "failed": _queue_failed,
    }

# ── Client (lazy init) ─────────────────────────────────────────────────────
_client = None

def _get_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client

    provider = os.getenv("OPENAI_PROVIDER", "openai").lower()

    if provider == "azure":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        api_version = os.getenv("AZURE_OPENAI_VERSION", "2024-02-15-preview")
        if not api_key or not endpoint:
            raise ValueError("Azure OpenAI credentials are not set in the environment variables.")
        _client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
    else:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set in the environment variables.")
        _client = AsyncOpenAI(api_key=api_key)

    return _client

# ── Core AI call with retry ────────────────────────────────────────────────
async def _call_openai(messages: list[dict], model: str) -> dict:
    """Call OpenAI with exponential backoff retry on transient errors."""
    client = _get_client()
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
                max_completion_tokens=1000,
            )
            content = response.choices[0].message.content
            return json.loads(content)

        except RateLimitError as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning("Rate limited (429), retry %d/%d in %.1fs", attempt, MAX_RETRIES, delay)
            await asyncio.sleep(delay)

        except (APITimeoutError, APIConnectionError) as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning("Connection error (%s), retry %d/%d in %.1fs", type(e).__name__, attempt, MAX_RETRIES, delay)
            await asyncio.sleep(delay)

        except APIError as e:
            if e.status_code in (500, 502, 503):
                last_error = e
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("Server error (%d), retry %d/%d in %.1fs", e.status_code, attempt, MAX_RETRIES, delay)
                await asyncio.sleep(delay)
            else:
                raise

        except json.JSONDecodeError:
            last_error = ValueError("Invalid JSON response")
            if attempt < MAX_RETRIES:
                logger.warning("Invalid JSON from AI, retry %d/%d", attempt, MAX_RETRIES)
                await asyncio.sleep(RETRY_BASE_DELAY)
            else:
                raise RuntimeError("AI service returned an invalid response after retries.")

    # All retries exhausted
    logger.error("All %d retries exhausted. Last error: %s", MAX_RETRIES, last_error)
    raise RuntimeError(f"AI service unavailable after {MAX_RETRIES} retries. Please try again later.")

# ── Public API ─────────────────────────────────────────────────────────────
async def generate_summary(text: str) -> dict:
    """Generate summary with concurrency queue and retry logic."""
    global _queue_waiting, _queue_processing, _queue_completed, _queue_failed

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages = [
        {"role": "system", "content": "You are a specialized medical assistant analyzing a 5-minute call transcript between a pharmacist and a patient. You must extract and return valid JSON containing exactly these three keys:\n1. 'summary': A concise paragraph summarizing the call.\n2. 'key_points': A list of strings representing the distinct key points discussed.\n3. 'action_items': A list of strings representing the required action items or next steps for either party.\nDo not include any other text or markdown formatting except the JSON object."},
        {"role": "user", "content": text}
    ]

    _queue_waiting += 1
    logger.info("Request queued (waiting: %d, processing: %d)", _queue_waiting, _queue_processing)

    try:
        async with _semaphore:
            _queue_waiting -= 1
            _queue_processing += 1
            logger.info("Processing started (waiting: %d, processing: %d)", _queue_waiting, _queue_processing)

            try:
                result = await _call_openai(messages, model)
                _queue_completed += 1
                return result
            except AuthenticationError:
                _queue_failed += 1
                logger.error("OpenAI authentication failed")
                raise ValueError("AI service authentication failed.")
            except Exception:
                _queue_failed += 1
                raise
            finally:
                _queue_processing -= 1
    except Exception:
        if _queue_waiting > 0:
            _queue_waiting -= 1
        raise
