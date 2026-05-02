import os
import pickle
import time
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from core.db_manager import DBManager


class YouTubeUploader:
    def __init__(self):
        self.db = DBManager()
        self.SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
        self.api_service_name = "youtube"
        self.api_version = "v3"
        self.client_secrets_file = "client_secrets.json"
        self.token_file = "token.pickle"
        self.youtube = self.get_authenticated_service()

        self.CATEGORY_MAP = {
            "space": "28",
            "tech_ai": "28",
            "psychology": "27",
            "health_wellness": "26",
            "animals_nature": "15",
            "finance_economy": "22",
            "bizarre_facts": "27",
            "history_world": "27",
            "motivation": "22",
            "general": "24",
        }

    def get_authenticated_service(self):
        creds = None
        if os.path.exists(self.token_file):
            with open(self.token_file, "rb") as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"⚠️ Token refresh failed: {e}. Re-authenticating...")
                    creds = None

            if not creds:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES
                )
                try:
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    print(
                        f"⚠️ Local server failed ({e}). Falling back to console auth..."
                    )
                    creds = flow.run_console()

            with open(self.token_file, "wb") as token:
                pickle.dump(creds, token)

        return build(self.api_service_name, self.api_version, credentials=creds)

    def upload_video(self):
        # 🟢 STRICT FIFO: Fetch the OLDEST packaged task first
        task = self.db.collection.find_one(
            {"status": "completed_packaged"}, sort=[("created_at", 1)]
        )

        if not task:
            return

        print(f"🚀 Starting Upload for: {task['title']}")

        video_path = task.get("final_video_path")
        if not video_path or not os.path.exists(video_path):
            print("❌ Error: Video file not found on disk.")
            self.db.collection.update_one(
                {"_id": task["_id"]}, {"$set": {"status": "upload_failed_missing_file"}}
            )
            return

        niche = task.get("niche", "general").lower()
        category_id = self.CATEGORY_MAP.get(niche, "22")

        ai_hashtags = task.get("ai_hashtags", "#Shorts").strip()
        if "#Shorts" not in ai_hashtags:
            ai_hashtags = "#Shorts " + ai_hashtags

        source_url = task.get("source_url", "")
        raw_description = (
            f"{task.get('ai_description', '')[:3800]}\n\n"
            f"Source: {source_url}\n\n"
            f"{ai_hashtags}"
        )
        clean_description = raw_description.replace("<", "").replace(">", "")

        # 🟢 SAFE TAG PARSING
        raw_tags = task.get("ai_tags", "")
        if isinstance(raw_tags, list):
            tag_list = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tag_list = [t.strip() for t in str(raw_tags).split(",") if t.strip()]

        final_tags = tag_list + ["Shorts", niche]
        while sum(len(t) for t in final_tags) > 480:
            final_tags.pop()

        request_body = {
            "snippet": {
                "categoryId": category_id,
                "title": task["title"][:100],
                "description": clean_description,
                "tags": final_tags,
            },
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
        }

        CHUNK_SIZE = 4 * 1024 * 1024
        media = MediaFileUpload(video_path, chunksize=CHUNK_SIZE, resumable=True)

        try:
            request = self.youtube.videos().insert(
                part="snippet,status", body=request_body, media_body=media
            )
        except Exception as e:
            print(f"❌ Failed to initialize upload request: {e}")
            return

        print("   ⏳ Uploading...")
        response = None
        retries = 0

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    print(f"      Uploaded {int(status.progress() * 100)}%")

            except HttpError as e:
                if e.resp.status in [400, 401, 403, 404]:
                    print(
                        f"      ❌ Fatal API Error ({e.resp.status}): {e.content.decode('utf-8')}"
                    )
                    self.db.collection.update_one(
                        {"_id": task["_id"]},
                        {"$set": {"status": "upload_failed_api_error"}},
                    )
                    return
                print(f"      ⚠️ Network Error ({e.resp.status}). Retrying in 5s...")
                retries += 1
                time.sleep(5)
                if retries > 10:
                    print("      ❌ Too many failures. Aborting.")
                    return
            except Exception as e:
                print(
                    f"      ⚠️ Connection interrupted ({type(e).__name__}). Retrying in 5s..."
                )
                retries += 1
                time.sleep(5)
                if retries > 10:
                    print("      ❌ Too many failures. Aborting.")
                    return

        if response and "id" in response:
            video_id = response["id"]
            print(f"   ✅ Upload Successful! Video ID: {video_id}")

            self.db.collection.update_one(
                {"_id": task["_id"]},
                {
                    "$set": {
                        "status": "uploaded",
                        "youtube_id": video_id,
                        "uploaded_at": datetime.now(timezone.utc),
                    }
                },
            )

            # 🟢 THE FIX: Keep FINAL_VIDEO_METADATA.txt, but delete the root metadata_<id>.txt
            try:
                # 1. Get the absolute path to your project root
                root_dir = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "..")
                )

                # 2. Target the specific root-level metadata file using the task's MongoDB ID
                root_metadata = os.path.join(root_dir, f"metadata_{task['_id']}.txt")

                # 3. Delete it if it exists
                if os.path.exists(root_metadata):
                    os.remove(root_metadata)
                    print(
                        f"   🧹 Cleaned up root file: {os.path.basename(root_metadata)}"
                    )

                print("   📁 Kept FINAL_VIDEO_METADATA.txt safely in the video folder.")

            except Exception as e:
                print(f"   ⚠️ Could not delete root metadata file: {e}")

        else:
            print(f"   ❌ Upload failed unexpectedly.")


if __name__ == "__main__":
    print("\n🚀 Manually starting YouTube Uploader...")
    uploader = YouTubeUploader()
    uploader.upload_video()
