from core.db_manager import DBManager


def fix_database():
    db = DBManager()

    # Delete ALL tasks to ensure a fresh start
    # This deletes 'pending', 'scripted', 'voiced', AND stuck 'ready_to_assemble' tasks
    result = db.collection.delete_many({"status": {"$ne": "uploaded"}})

    print(f"✅ Database Wiped. Deleted {result.deleted_count} old/stuck tasks.")
    print("🚀 You can now run 'main.py' for a fresh start.")


if __name__ == "__main__":
    fix_database()
