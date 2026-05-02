import os
from groq import Groq, AsyncGroq
from tenacity import retry, wait_exponential, stop_after_attempt
from dotenv import load_dotenv

load_dotenv()


def _log_retry(retry_state):
    """Custom printer to maintain terminal visibility during backoffs."""
    sleep_time = retry_state.next_action.sleep
    attempt = retry_state.attempt_number
    exception = retry_state.outcome.exception()
    print(
        f"      ⏳ Groq API limit/error ({type(exception).__name__}). Retrying in {sleep_time:.1f}s... (Attempt {attempt}/5)"
    )


class AIEngine:
    def __init__(self, **kwargs):
        # Allow **kwargs so existing callers don't break
        self.groq_key = os.getenv("GROQ_API_KEY")
        if not self.groq_key:
            print("⚠️ GROQ_API_KEY missing from .env file.")

        # Initialize BOTH sync and async clients to guarantee backward compatibility
        self.groq_client = Groq(api_key=self.groq_key)
        self.async_groq_client = AsyncGroq(api_key=self.groq_key)

        # Prioritize kwarg override, default to versatile model
        self.groq_model = kwargs.get("primary_model", "llama-3.3-70b-versatile")

    # ─────────────────────────────────────────────
    # MAIN GENERATE METHODS (Sync & Async)
    # ─────────────────────────────────────────────

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=60),  # Waits 3s, 6s, 12s, 24s...
        stop=stop_after_attempt(5),
        before_sleep=_log_retry,
        reraise=True,
    )
    def generate(self, system_prompt, user_prompt, require_json=False):
        """
        Synchronous generation (Backward Compatible).
        Used by existing modules like brain.py until they are upgraded.
        """
        kwargs = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "model": self.groq_model,
            "timeout": 30.0,  # Prevent infinite hanging
        }
        if require_json:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.groq_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=60),
        stop=stop_after_attempt(5),
        before_sleep=_log_retry,
        reraise=True,
    )
    async def agenerate(self, system_prompt, user_prompt, require_json=False):
        """
        Asynchronous generation (New Capability).
        Will be utilized by upgraded modules (visuals.py/scraper.py) to run LLM tasks in parallel safely.
        """
        kwargs = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "model": self.groq_model,
            "timeout": 30.0,
        }
        if require_json:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self.async_groq_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content
