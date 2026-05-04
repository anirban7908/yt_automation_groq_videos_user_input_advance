import os
from core.db_manager import DBManager


class UploadManager:
    def __init__(self):
        self.db = DBManager()

    def prepare_package(self):
        # Find the task that just finished assembly
        task = self.db.collection.find_one({"status": "ready_to_upload"})
        if not task:
            print("No videos ready for upload prep.")
            return False

        folder = task.get("folder_path")
        if not folder or not os.path.exists(folder):
            print(f"⚠️ Error: Folder path missing for task {task['_id']}")
            return False

        print(
            f"📦 Packaging final video assets for: {task.get('title', 'Untitled')[:50]}..."
        )

        title = task.get("title", "Untitled")
        desc = task.get("ai_description", "No description generated.")

        # Safely handle tags whether they are a string or a list
        raw_tags = task.get("ai_tags", "")
        if isinstance(raw_tags, list):
            tags = ", ".join([str(t).strip() for t in raw_tags])
        else:
            tags = raw_tags

        # 🟢 THE FIX: Save the metadata exactly inside the isolated video folder
        # We name it FINAL_VIDEO_METADATA.txt so the assembler cleanup ignores it
        metadata_path = os.path.join(folder, "FINAL_VIDEO_METADATA.txt")

        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                f.write(f"TITLE: {title}\n")
                f.write("-" * 40 + "\n")
                f.write(f"DESCRIPTION:\n{desc}\n")
                f.write("-" * 40 + "\n")
                f.write(f"TAGS: {tags}\n")

            print(
                f"   📝 Metadata written cleanly to folder: {os.path.basename(folder)}"
            )

        except Exception as e:
            print(f"   ❌ Failed to write metadata file: {e}")
            return False

        # Advance the status so the Uploader grabs it
        self.db.collection.update_one(
            {"_id": task["_id"]}, {"$set": {"status": "completed_packaged"}}
        )
        print("   ✅ Packaging complete! Ready for YouTube.")
