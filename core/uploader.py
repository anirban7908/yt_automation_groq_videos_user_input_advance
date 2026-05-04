import os
import pickle
import time
from datetime import datetime, timezone

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from core.db_manager import DBManager

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class YouTubeUploader:
    def __init__(self):
        self.db = DBManager()
        self.SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
        self.api_service_name = "youtube"
        self.api_version = "v3"
        self.client_secrets_file = os.getenv(
            "YOUTUBE_CLIENT_SECRETS_FILE",
            os.path.join(PROJECT_ROOT, "client_secrets.json"),
        )
        self.token_file = os.getenv(
            "YOUTUBE_TOKEN_FILE", os.path.join(PROJECT_ROOT, "token.pickle")
        )
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
            "war_news": "25",
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
                    print(f"Token refresh failed: {e}. Re-authenticating...")
                    creds = None

            if not creds:
                if not os.path.exists(self.client_secrets_file):
                    raise FileNotFoundError(
                        f"YouTube client secrets not found: {self.client_secrets_file}"
                    )

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES
                )
                try:
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    print(f"Local auth server failed ({e}). Falling back to console auth.")
                    creds = flow.run_console()

            with open(self.token_file, "wb") as token:
                pickle.dump(creds, token)

        return build(self.api_service_name, self.api_version, credentials=creds)

    def upload_video(self):
        task = self.db.collection.find_one(
            {"status": "completed_packaged"}, sort=[("created_at", 1)]
        )
        if not task:
            print("No packaged videos waiting for upload.")
            return False

        print(f"Starting YouTube upload for: {task['title']}")

        video_path = task.get("final_video_path")
        if not video_path or not os.path.exists(video_path):
            print("Error: video file not found on disk.")
            self.db.collection.update_one(
                {"_id": task["_id"]},
                {"$set": {"status": "upload_failed_missing_file"}},
            )
            return False

        niche = task.get("niche", "general").lower()
        category_id = self.CATEGORY_MAP.get(niche, "22")

        ai_hashtags = task.get("ai_hashtags", "#Shorts").strip()
        if "#Shorts" not in ai_hashtags:
            ai_hashtags = "#Shorts " + ai_hashtags

        raw_description = (
            f"{task.get('ai_description', '')[:3800]}\n\n"
            f"Source: {task.get('source_url', '')}\n\n"
            f"{ai_hashtags}"
        )
        clean_description = raw_description.replace("<", "").replace(">", "")

        raw_tags = task.get("ai_tags", "")
        if isinstance(raw_tags, list):
            tag_list = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tag_list = [t.strip() for t in str(raw_tags).split(",") if t.strip()]

        final_tags = tag_list + ["Shorts", niche]
        while final_tags and sum(len(t) for t in final_tags) > 480:
            final_tags.pop()

        request_body = {
            "snippet": {
                "categoryId": category_id,
                "title": task["title"][:100],
                "description": clean_description,
                "tags": final_tags,
            },
            "status": {
                "privacyStatus": os.getenv("YOUTUBE_PRIVACY_STATUS", "private"),
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(video_path, chunksize=4 * 1024 * 1024, resumable=True)

        try:
            request = self.youtube.videos().insert(
                part="snippet,status", body=request_body, media_body=media
            )
        except Exception as e:
            print(f"Failed to initialize upload request: {e}")
            return False

        response = None
        retries = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    print(f"Uploaded {int(status.progress() * 100)}%")
            except HttpError as e:
                if e.resp.status in [400, 401, 403, 404]:
                    print(f"Fatal YouTube API error ({e.resp.status}): {e.content}")
                    self.db.collection.update_one(
                        {"_id": task["_id"]},
                        {"$set": {"status": "upload_failed_api_error"}},
                    )
                    return False
                retries += 1
                if retries > 10:
                    print("Too many upload failures. Aborting.")
                    return False
                print(f"Recoverable upload error ({e.resp.status}). Retrying in 5s.")
                time.sleep(5)
            except Exception as e:
                retries += 1
                if retries > 10:
                    print("Too many connection failures. Aborting.")
                    return False
                print(f"Upload interrupted ({type(e).__name__}). Retrying in 5s.")
                time.sleep(5)

        if not response or "id" not in response:
            print("Upload failed unexpectedly.")
            return False

        video_id = response["id"]
        print(f"Upload successful. Video ID: {video_id}")

        folder = task.get("folder_path")
        thumbnail_path = os.path.join(folder, "thumbnail.jpg") if folder else None
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                self.youtube.thumbnails().set(
                    videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
                ).execute()
                print("Thumbnail set successfully.")
            except Exception as e:
                print(f"Thumbnail upload failed: {e}")

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

        root_metadata = os.path.join(PROJECT_ROOT, f"metadata_{task['_id']}.txt")
        try:
            if os.path.exists(root_metadata):
                os.remove(root_metadata)
                print(f"Cleaned root metadata file: {os.path.basename(root_metadata)}")
        except Exception as e:
            print(f"Could not delete root metadata file: {e}")

        return True


if __name__ == "__main__":
    print("\nManually starting YouTube uploader...")
    uploader = YouTubeUploader()
    uploader.upload_video()
