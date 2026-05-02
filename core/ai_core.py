import os
import re
import time
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


class AIEngine:
    def __init__(self, **kwargs):
        # Accepts **kwargs so existing callers passing primary_model= don't break
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            print("⚠️ GROQ_API_KEY missing from .env file.")
        self.groq_client = Groq(api_key=groq_key)
        self.groq_model = "llama-3.3-70b-versatile"

    # ─────────────────────────────────────────────
    # RATE LIMIT HELPERS
    # ─────────────────────────────────────────────

    def _parse_retry_wait(self, error_message):
        """
        Parses Groq rate-limit error messages and returns seconds to wait.
        Handles format: 'Please try again in 10m1.344s' or 'in 30.5s'
        Returns 0 if no wait time found. Capped at 15 minutes.
        """
        try:
            msg = str(error_message)
            # Match "Xm Y.Zs" → minutes + seconds
            match = re.search(r"(\d+)m([\d.]+)s", msg)
            if match:
                total = int(match.group(1)) * 60 + float(match.group(2))
                return min(total, 900)
            # Match plain "X.Ys" → seconds only
            match = re.search(r"in ([\d.]+)s", msg)
            if match:
                return min(float(match.group(1)), 900)
        except Exception:
            pass
        return 0

    def _is_rate_limit(self, error):
        """Returns True if the error is a 429 / rate-limit error."""
        msg = str(error).lower()
        return "429" in msg or "rate_limit" in msg or "rate limit" in msg

    # ─────────────────────────────────────────────
    # MAIN GENERATE METHOD
    # ─────────────────────────────────────────────

    def generate(self, system_prompt, user_prompt, require_json=False):
        """
        Sends a prompt to Groq Llama 3.3 and returns the response text.

        On rate limit: parses the exact retry delay from the error message,
        waits that long, then retries once before raising.

        Args:
            system_prompt  : The system/role instruction for the model.
            user_prompt    : The user message / task.
            require_json   : If True, instructs the model to return JSON only.

        Returns:
            str — the model's response text.

        Raises:
            RuntimeError if Groq is unavailable after retry.
        """

        def _call():
            kwargs = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "model": self.groq_model,
            }
            if require_json:
                kwargs["response_format"] = {"type": "json_object"}
            response = self.groq_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content

        # ── First attempt ──
        try:
            return _call()
        except Exception as e:
            if self._is_rate_limit(e):
                wait = self._parse_retry_wait(e)
                if wait > 0:
                    print(
                        f"      ⏳ Groq rate limited. Waiting {wait:.0f}s then retrying..."
                    )
                    time.sleep(wait)
                    try:
                        return _call()
                    except Exception as retry_e:
                        print(f"      ❌ Groq retry also failed: {retry_e}")
                        raise RuntimeError(
                            f"Groq is rate-limited and retry failed: {retry_e}"
                        )
            print(f"      ❌ Groq call failed: {e}")
            raise RuntimeError(f"Groq call failed: {e}")
