import os
import requests
import urllib.parse
import time
from dotenv import load_dotenv

load_dotenv()


def generate_with_retry(url, headers, test_path, max_retries=3):
    """
    Attempts to download the image, retrying if a 530 or server error occurs.
    """
    for i in range(max_retries):
        try:
            print(f"📡 Attempt {i+1}: Requesting image...")
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 200 and "image" in response.headers.get(
                "Content-Type", ""
            ):
                with open(test_path, "wb") as f:
                    f.write(response.content)
                return True

            elif response.status_code == 530:
                print(
                    f"⚠️ Server is 'Frozen' (Error 530). Waiting 10 seconds to retry..."
                )
            else:
                print(f"❌ Server returned Status: {response.status_code}")
                print(f"Response: {response.text[:100]}")

        except Exception as e:
            print(f"❌ Connection Error: {e}")

        time.sleep(10)  # Wait before next attempt
    return False


def test_system():
    api_key = os.getenv("POLLINATIONS_API_KEY")
    test_path = "test_ai_final.jpg"
    prompt = "A cinematic sports stadium, high action, realistic lighting, 8k"

    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true&private=true"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    success = generate_with_retry(url, headers, test_path)

    if success:
        print(f"✅ SUCCESS! Image saved as {test_path}")
    else:
        print(
            "💥 FAILED after multiple attempts. The Pollinations server might be down."
        )


if __name__ == "__main__":
    test_system()
