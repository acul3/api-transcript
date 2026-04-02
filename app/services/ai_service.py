import os
import json
import logging
from openai import AsyncOpenAI, AsyncAzureOpenAI, APIError, AuthenticationError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

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

async def generate_summary(text: str) -> dict:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = _get_client()

    messages = [
        {"role": "system", "content": "You are a specialized medical assistant analyzing a 5-minute call transcript between a pharmacist and a patient. You must extract and return valid JSON containing exactly these three keys:\n1. 'summary': A concise paragraph summarizing the call.\n2. 'key_points': A list of strings representing the distinct key points discussed.\n3. 'action_items': A list of strings representing the required action items or next steps for either party.\nDo not include any other text or markdown formatting except the JSON object."},
        {"role": "user", "content": text}
    ]

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_completion_tokens=1000
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except AuthenticationError:
        logger.error("OpenAI authentication failed")
        raise ValueError("AI service authentication failed.")
    except APIError as e:
        logger.error("OpenAI API error: %s", e)
        raise RuntimeError("AI service is temporarily unavailable.")
    except json.JSONDecodeError:
        logger.error("Failed to parse AI response as JSON")
        raise RuntimeError("AI service returned an invalid response.")
