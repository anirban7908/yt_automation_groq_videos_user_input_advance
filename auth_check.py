import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Must match the scope used in uploader.py exactly
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
CLIENT_SECRETS_FILE = os.getenv(
    "YOUTUBE_CLIENT_SECRETS_FILE", os.path.join(PROJECT_ROOT, "client_secrets.json")
)
TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", os.path.join(PROJECT_ROOT, "token.pickle"))


def verify_and_refresh_token():
    """
    Checks if token.pickle exists and is valid.
    If expired, attempts to refresh.
    If missing or unrefreshable, prompts the user to re-authenticate.
    """
    print("\n🔐 Running YouTube Auth Pre-Flight Check...")
    creds = None

    # 1. Load existing token if it exists
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

    # 2. Check validity
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("   ⚠️ Token expired. Attempting to refresh silently...")
            try:
                creds.refresh(Request())
                print("   ✅ Token successfully refreshed!")
            except Exception as e:
                print(f"   ❌ Refresh failed: {e}. Full re-authentication required.")
                creds = None
        else:
            print("   ⚠️ No valid token found. Authentication required.")

        # 3. Request new auth if missing or refresh failed
        if not creds:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                print(f"   🚨 CRITICAL ERROR: '{CLIENT_SECRETS_FILE}' not found!")
                print(
                    "   Please download it from Google Cloud Console and place it in the root folder."
                )
                return False

            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES
            )

            try:
                print("   🌐 Opening browser for authentication...")
                creds = flow.run_local_server(port=0)
            except Exception as e:
                print(
                    f"   ⚠️ Local server failed ({e}). Falling back to console auth..."
                )
                creds = flow.run_console()

        # 4. Save the valid credentials back to the pickle file
        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)
            print("   💾 New token saved safely.")

    if creds and creds.valid:
        print("✅ YouTube Authentication is ACTIVE and VALID.\n")
        return True
    else:
        print("❌ YouTube Authentication FAILED.\n")
        return False


if __name__ == "__main__":
    verify_and_refresh_token()
