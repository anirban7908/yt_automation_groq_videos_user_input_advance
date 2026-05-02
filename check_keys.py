import os
import requests
from dotenv import load_dotenv

load_dotenv()


def check_keys():
    print("🔑 Checking API Keys...")
    hf_token = os.getenv("HUGGINGFACE_API_KEY")
    if not hf_token:
        print("❌ HUGGINGFACE_API_KEY is MISSING")
    else:
        # NEW URL
        url = "https://router.huggingface.co/hf-inference/models/google-bert/bert-base-uncased"
        headers = {"Authorization": f"Bearer {hf_token}"}
        res = requests.post(url, headers=headers, json={"inputs": "Test"})

        if res.status_code == 200:
            print("✅ Hugging Face Key is VALID.")
        else:
            print(f"❌ HF Error: {res.status_code} - {res.text}")

    pexels_key = os.getenv("PEXELS_API_KEY")
    if pexels_key:
        res = requests.get(
            "https://api.pexels.com/v1/search?query=test",
            headers={"Authorization": pexels_key},
        )
        if res.status_code == 200:
            print("✅ Pexels Key is VALID.")
        else:
            print(f"❌ Pexels Error: {res.status_code}")


if __name__ == "__main__":
    check_keys()
