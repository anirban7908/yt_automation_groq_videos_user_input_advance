import os
import time
import requests
from dotenv import load_dotenv

# Load your new IDs and Token from the .env file
load_dotenv()
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
IG_USER_ID = os.getenv("IG_USER_ID")

# The Meta Graph API version
API_VERSION = "v20.0"


def upload_to_facebook(video_path, description):
    """
    Uploads a local video file directly to your Facebook Page.
    """
    url = f"https://graph.facebook.com/{API_VERSION}/{FB_PAGE_ID}/videos"

    payload = {"access_token": META_ACCESS_TOKEN, "description": description}

    print(f"📤 Uploading {video_path} to Facebook Page...")
    try:
        with open(video_path, "rb") as video_file:
            files = {"source": video_file}
            response = requests.post(url, data=payload, files=files)

        result = response.json()
        if "id" in result:
            print(f"✅ Facebook upload successful! FB Video ID: {result['id']}")
            return True
        else:
            print(f"❌ Facebook upload failed: {result}")
            return False
    except Exception as e:
        print(f"❌ Error during Facebook upload: {e}")
        return False


def upload_to_instagram(video_url, caption):
    """
    Uploads a video to Instagram as a Reel.
    🚨 IMPORTANT: Instagram's API strictly requires a PUBLIC URL (video_url).
    It cannot accept direct local file uploads like Facebook does.
    """
    # Step 1: Initialize the upload container
    creation_url = f"https://graph.facebook.com/{API_VERSION}/{IG_USER_ID}/media"
    creation_payload = {
        "access_token": META_ACCESS_TOKEN,
        "video_url": video_url,
        "caption": caption,
        "media_type": "REELS",
    }

    print(f"📤 Initializing Instagram Reel container...")
    creation_response = requests.post(creation_url, data=creation_payload)
    creation_data = creation_response.json()

    if "id" not in creation_data:
        print(f"❌ Instagram container creation failed: {creation_data}")
        return False

    creation_id = creation_data["id"]
    print(
        f"⏳ Container created (ID: {creation_id}). Waiting for Meta to process the video..."
    )

    # Step 2: Poll the API until the video is done processing
    status_url = f"https://graph.facebook.com/{API_VERSION}/{creation_id}?fields=status_code&access_token={META_ACCESS_TOKEN}"

    while True:
        status_response = requests.get(status_url)
        status_data = status_response.json()
        status = status_data.get("status_code")

        if status == "FINISHED":
            print("✅ Video processed successfully by Instagram!")
            break
        elif status == "ERROR":
            print(
                "❌ Instagram video processing failed (Check URL validity/video format)."
            )
            return False

        print("   ...still processing, waiting 5 seconds...")
        time.sleep(5)

    # Step 3: Publish the container to the Instagram feed
    publish_url = f"https://graph.facebook.com/{API_VERSION}/{IG_USER_ID}/media_publish"
    publish_payload = {"access_token": META_ACCESS_TOKEN, "creation_id": creation_id}

    print("🚀 Publishing Reel to Instagram...")
    publish_response = requests.post(publish_url, data=publish_payload)
    publish_data = publish_response.json()

    if "id" in publish_data:
        print(f"✅ Instagram publish successful! IG Post ID: {publish_data['id']}")
        return True
    else:
        print(f"❌ Instagram publish failed: {publish_data}")
        return False


# --- Quick Test Block (Only runs if you execute this file directly) ---
if __name__ == "__main__":
    test_caption = "Testing my new automation setup! 🤖✨ #automation #bot"

    # Test Facebook (Point this to a real local video file)
    # upload_to_facebook("generated_video_01.mp4", test_caption)

    # Test Instagram (Point this to a real public URL)
    # upload_to_instagram("https://www.w3schools.com/html/mov_bbb.mp4", test_caption)
