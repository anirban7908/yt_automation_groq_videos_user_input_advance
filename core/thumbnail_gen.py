import os
import requests
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class ThumbnailGenerator:
    def __init__(self):
        self.api_url = "https://image.pollinations.ai/prompt/"
        # 1. Load the API key from .env (optional but helpful if you have a Pro key)
        self.api_key = os.getenv("POLLINATIONS_API_KEY")

    def _generate_with_safe_retry(self, url, headers, save_path, max_retries=3):
        """
        Modified safety logic from test_pollinations.py.
        Anticipates the server is 'Frozen' (Error 530) and waits for it to wake up.
        """
        for i in range(max_retries):
            try:
                if i > 0:
                    print(f"📡      - Attempt {i+1}/{max_retries}...")

                response = requests.get(url, headers=headers, timeout=45)

                if response.status_code == 200 and "image" in response.headers.get(
                    "Content-Type", ""
                ):
                    # Success: Save the image content
                    with open(save_path, "wb") as f:
                        f.write(response.content)
                    return True

                elif response.status_code == 530:
                    # Smart Retry: Server is asleep. Wait 10s before trying again.
                    print(
                        f"⚠️      - Pollinations server is 'Frozen' (530). Waiting 10s for it to wake up..."
                    )
                    time.sleep(10)
                    continue

                else:
                    # Unrecoverable error (e.g., 400 or 403)
                    print(
                        f"❌      - Server error ({response.status_code}): {response.text[:100]}"
                    )
                    break

            except Exception as e:
                print(f"❌      - Connection Error during generation: {e}")
                time.sleep(5)  # Brief wait after network interruption

        return False

    def generate_thumbnail(self, task_folder, title, keywords):
        print(f"🎨 Generating thumbnail via Pollinations.ai...")

        # 2. Craft a high-impact professional prompt
        clean_title = title.replace(":", "").replace("'", "").strip()

        # We ensure a list or handle strings safely
        if isinstance(keywords, list):
            prompt_tags = ", ".join(keywords[:4])  # Grab top 4 keywords
        elif keywords:
            prompt_tags = keywords
        else:
            prompt_tags = "breaking news, trending, important"

        # Build the final prompt with style modifiers
        # Note: We now add 'nologo=true&private=true' as in your test script
        full_prompt = f"{clean_title}, {prompt_tags}, cinematic sports stadium action shot, high contrast, bold colors, realistic lighting, 8k, highly detailed, professional YouTube thumbnail"
        encoded_prompt = requests.utils.quote(full_prompt)

        # We keep the size parameters (1280x720) as requested by other integrated modules
        request_url = f"{self.api_url}{encoded_prompt}?width=1280&height=720&model=flux&nologo=true&private=true"

        # 3. Apply the Authorization header if the key exists
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        save_path = os.path.join(task_folder, "thumbnail.jpg")

        # 4. Trigger the safe retry loop
        success = self._generate_with_safe_retry(request_url, headers, save_path)

        if success:
            print(f"   ✅ Thumbnail saved: {os.path.basename(save_path)}")
            return save_path
        else:
            print(f"   ❌ Thumbnail generation failed after retries.")
            return None
