import sys
import asyncio
import argparse
import json
import os
import datetime
import threading

from core.scraper import NewsScraper
from core.brain import ScriptGenerator
from core.voice import VoiceEngine
from core.visuals import VisualScout
from core.assembler import VideoAssembler
from core.upload_prep import UploadManager
from core.uploader import YouTubeUploader
from core.db_manager import DBManager

# 🟢 NEW: Import the modular standalone auth checker
from auth_check import verify_and_refresh_token

from core.thumbnail_gen import ThumbnailGenerator
from core.meta_uploader import upload_to_facebook, upload_to_instagram

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
MODE_TIMEOUT = 60
PROMPT_TIMEOUT = 300


# ─────────────────────────────────────────────────────────────
# TIMED INPUT HELPERS (Preserved for cross-platform compatibility)
# ─────────────────────────────────────────────────────────────


def _timed_input(prompt, timeout=MODE_TIMEOUT, default=""):
    result = [default]
    input_received = threading.Event()

    def _get_input():
        try:
            val = input(prompt)
            result[0] = val
        except EOFError:
            result[0] = default
        finally:
            input_received.set()

    thread = threading.Thread(target=_get_input, daemon=True)
    thread.start()

    for remaining in range(timeout, 0, -1):
        if input_received.is_set():
            break
        print(
            f"\r   ⏳ Auto-selecting in {remaining:2d}s... (press Enter to stop timer)",
            end="",
            flush=True,
        )
        input_received.wait(timeout=1)

    if not input_received.is_set():
        print(f"\r   ⏰ Time's up! Auto-selecting default: '{default}'{' ' * 20}")
        result[0] = default

    thread.join(timeout=0.1)
    return result[0].strip()


def _prompt(prompt_text, timeout=PROMPT_TIMEOUT, default=""):
    if sys.platform == "win32":
        import msvcrt

        result = [default]
        timer_cancelled = threading.Event()
        input_done = threading.Event()

        def _watch_keys():
            while not input_done.is_set():
                if msvcrt.kbhit():
                    timer_cancelled.set()
                    break
                input_done.wait(timeout=0.05)

        watcher = threading.Thread(target=_watch_keys, daemon=True)
        watcher.start()

        for remaining in range(timeout, 0, -1):
            if timer_cancelled.is_set() or input_done.is_set():
                break
            print(
                f"\r   ⏳ Auto-selecting in {remaining:3d}s... (start typing to cancel)",
                end="",
                flush=True,
            )
            timer_cancelled.wait(timeout=1)

        if not timer_cancelled.is_set() and not input_done.is_set():
            print(f"\r   ⏰ Time's up! Auto-selecting default: '{default}'{' ' * 30}")
            input_done.set()
            return default

        print(f"\r{' ' * 70}\r", end="", flush=True)
        print(prompt_text, end="", flush=True)
        try:
            val = input()
            result[0] = val
        except EOFError:
            result[0] = default
        finally:
            input_done.set()

        return result[0].strip()

    else:
        import select

        result = [default]
        timer_cancelled = threading.Event()
        input_done = threading.Event()

        def _watch_stdin():
            try:
                while not input_done.is_set():
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        timer_cancelled.set()
                        break
            except Exception:
                timer_cancelled.set()

        watcher = threading.Thread(target=_watch_stdin, daemon=True)
        watcher.start()

        for remaining in range(timeout, 0, -1):
            if timer_cancelled.is_set() or input_done.is_set():
                break
            print(
                f"\r   ⏳ Auto-selecting in {remaining:3d}s... (start typing to cancel)",
                end="",
                flush=True,
            )
            timer_cancelled.wait(timeout=1)

        if not timer_cancelled.is_set() and not input_done.is_set():
            print(f"\r   ⏰ Time's up! Auto-selecting default: '{default}'{' ' * 30}")
            input_done.set()
            return default

        print(f"\r{' ' * 70}\r", end="", flush=True)
        print(prompt_text, end="", flush=True)
        try:
            val = input()
            result[0] = val
        except EOFError:
            result[0] = default
        finally:
            input_done.set()

        return result[0].strip()


def _ask_mode():
    print(f"\n{'━' * 48}")
    print("  🎬  YOUTUBE AUTOMATION PIPELINE")
    print(f"{'━' * 48}")
    print("\n  How do you want to run the pipeline?\n")
    print("    [M]  Manual   — You review & approve each step")
    print("    [A]  Automatic — Fully hands-free, end to end")
    print(f"\n  Default: Automatic (after {MODE_TIMEOUT} seconds)\n")

    choice = _timed_input("→ ", timeout=MODE_TIMEOUT, default="A").upper()

    if choice in ("M", "MANUAL"):
        print("\n🛠️  Manual mode selected. You're in control!\n")
        return True
    else:
        print("\n🤖  Automatic mode selected. Running hands-free...\n")
        return False


# ─────────────────────────────────────────────────────────────
# DB CLEANUP HELPERS
# ─────────────────────────────────────────────────────────────


def _delete_task(db, task_id, reason=""):
    try:
        db.collection.delete_one({"_id": task_id})
        print(f"🗑️  Task deleted from DB. Reason: {reason}")
    except Exception as e:
        print(f"⚠️  Could not delete task from DB: {e}")


def _cleanup_task_files(task):
    import shutil

    folder = task.get("folder_path", "")
    if folder and os.path.exists(folder):
        try:
            shutil.rmtree(folder)
            print(f"🗑️  Deleted task folder: {folder}")
        except Exception as e:
            print(f"⚠️  Could not delete folder {folder}: {e}")


# ─────────────────────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────


def _display_topics(result):
    niche = result["niche"].upper()
    topics = result["topics"]
    print(f"\n{'━' * 48}")
    print(f"  🎯 AI picked 3 viral topics  [NICHE: {niche}]")
    print(f"{'━' * 48}")
    for i, t in enumerate(topics, 1):
        print(f"\n  [{i}] {t['title']}")
        print(f"      💡 {t['reason']}")
        if t.get("summary"):
            summary = t["summary"].replace("\n", " ").strip()
            if len(summary) > 120:
                summary = summary[:117] + "..."
            print(f"      📄 {summary}")
    print(f"\n{'━' * 48}")


def _display_script(data):
    title = data.get("title", "Untitled")
    scenes = data.get("scenes", [])
    print(f"\n{'━' * 48}")
    print(f'  📋 GENERATED SCRIPT — "{title}"')
    print(f"{'━' * 48}")
    for i, scene in enumerate(scenes, 1):
        text = scene.get("text", "")
        keywords = scene.get("keywords", [])
        print(f"\n  Scene {i}: {text}")
        print(f"           🔍 Keywords: {', '.join(keywords)}")
    print(f"\n{'━' * 48}")


# ─────────────────────────────────────────────────────────────
# APPROVAL LOOPS
# ─────────────────────────────────────────────────────────────


def run_topic_approval(scraper, slot_name):
    MAX_REGEN = 5
    regen_count = 0

    while True:
        print("\n🔍 Fetching viral topics from RSS feeds...")
        result = scraper.fetch_and_present_topics(slot_name)

        if not result:
            print(
                "❌ Could not fetch topics. Check your internet connection and try again."
            )
            return None

        _display_topics(result)
        topics = result["topics"]

        print("👉 Enter 1 / 2 / 3 to select  |  R to regenerate  |  Q to quit")
        choice = _prompt("   → ", default="1").upper()

        if choice == "Q":
            print("⛔ Pipeline aborted by user.")
            return None
        elif choice == "R":
            regen_count += 1
            if regen_count >= MAX_REGEN:
                print(
                    f"\n⚠️  Max regenerations ({MAX_REGEN}) reached. Auto-selecting [1]."
                )
                choice = "1"
            else:
                print(f"🔄 Regenerating... ({MAX_REGEN - regen_count} left)")
                continue

        if choice in ("1", "2", "3"):
            idx = int(choice) - 1
            chosen = topics[idx]
            print(f"\n✅ Selected: \"{chosen['title'][:70]}\"")
            print("📖 Deep-reading full article...")
            task = scraper.save_approved_topic(
                chosen, result["niche"], result["niche_data"], slot_name
            )
            if task:
                print(f"💾 Task saved to DB.")
                return task
            else:
                print("⚠️ Could not save task (possible duplicate). Regenerating...")
                continue
        else:
            print("⚠️ Invalid input. Please enter 1, 2, 3, R, or Q.")


def run_script_approval(brain, task):
    MAX_REGEN = 5
    regen_count = 0
    feedback = ""
    data = None

    while True:
        if data is None or regen_count > 0:
            print(f"\n🧠 Generating script{' with feedback' if feedback else ''}...")
            if feedback:
                data = brain.regenerate_with_feedback(task, feedback)
            else:
                data = brain.generate_script_for_task(task)

            if not data:
                print("❌ Script generation failed. Retrying...")
                regen_count += 1
                if regen_count >= MAX_REGEN:
                    return False
                continue

        _display_script(data)
        print("👉 A to approve  |  R to regenerate  |  N to add notes  |  Q to quit")
        choice = _prompt("   → ", default="A").upper()

        if choice == "Q":
            return False
        elif choice == "A":
            print("\n✅ Script approved! Saving to database...")
            brain.approve_and_save(task, data)
            return True
        elif choice == "R":
            regen_count += 1
            feedback = ""
            if regen_count >= MAX_REGEN:
                brain.approve_and_save(task, data)
                return True
            data = None
        elif choice == "N":
            regen_count += 1
            if regen_count >= MAX_REGEN:
                brain.approve_and_save(task, data)
                return True
            print("💬 What should the AI change or improve?")
            feedback = _prompt("   → ", default="").strip()
            data = None
        else:
            print("⚠️ Invalid input.")


# ─────────────────────────────────────────────────────────────
# SHARED POST-SCRIPT PIPELINE
# ─────────────────────────────────────────────────────────────


def _run_post_script_steps(task, db, slot_name, is_manual):
    print("---------------------------------------")
    try:
        voice = VoiceEngine()
        asyncio.run(voice.generate_audio())
        if not db.collection.find_one({"_id": task["_id"], "status": "voiced"}):
            raise RuntimeError("Status not updated to 'voiced'")
    except Exception as e:
        print(f"❌ Voice generation failed: {e}")
        _delete_task(db, task["_id"], f"Voice error: {e}")
        _cleanup_task_files(task)
        return False

    print("---------------------------------------")
    try:
        visuals = VisualScout()
        visuals.download_visuals()
        if not db.collection.find_one(
            {"_id": task["_id"], "status": "ready_to_assemble"}
        ):
            raise RuntimeError("Status not updated to 'ready_to_assemble'")
    except Exception as e:
        print(f"❌ Visuals failed: {e}")
        _delete_task(db, task["_id"], f"Visuals error: {e}")
        _cleanup_task_files(task)
        return False

    print("---------------------------------------")
    try:
        assembler = VideoAssembler()
        assembler.assemble()
        if not db.collection.find_one(
            {"_id": task["_id"], "status": "ready_to_upload"}
        ):
            raise RuntimeError("Status not updated to 'ready_to_upload'")
    except Exception as e:
        print(f"❌ Assembly failed: {e}")
        _delete_task(db, task["_id"], f"Assembler error: {e}")
        _cleanup_task_files(task)
        return False

    print("---------------------------------------")
    try:
        prep = UploadManager()
        prep.prepare_package()
        if not db.collection.find_one(
            {"_id": task["_id"], "status": "completed_packaged"}
        ):
            raise RuntimeError("Status not updated to 'completed_packaged'")
    except Exception as e:
        print(f"❌ Upload prep failed: {e}")
        _delete_task(db, task["_id"], f"Prep error: {e}")
        _cleanup_task_files(task)
        return False

    print("---------------------------------------")
    try:
        from core.thumbnail_gen import ThumbnailGenerator

        thumb_gen = ThumbnailGenerator()

        # Build the absolute path using PROJECT_ROOT
        folder = os.path.normpath(os.path.join(PROJECT_ROOT, task["folder_path"]))

        # Generate the image
        thumb_gen.generate_thumbnail(folder, task["title"], task.get("ai_tags", []))
    except Exception as e:
        print(f"⚠️ Thumbnail generation skipped: {e}")

    print("---------------------------------------")
    youtube_success = False
    try:
        uploader = YouTubeUploader()
        youtube_success = uploader.upload_video()
    except Exception as e:
        print(f"❌ YouTube upload failed: {e}")
        return False

    if not youtube_success:
        print("YouTube upload did not complete. Skipping cross-posting and final success log.")
        return False

    if youtube_success:
        print("---------------------------------------")
        print("📱 STARTING META CROSS-POSTING (FB & IG)")

        video_path = os.path.normpath(
            os.path.join(PROJECT_ROOT, task["folder_path"], "FINAL_VIDEO.mp4")
        )
        caption = f"{task['title']}\n\n#automation #AI #trending"

        # 1. Facebook
        try:
            upload_to_facebook(video_path, caption)
        except Exception as e:
            print(f"⚠️ Meta FB Error: {e}")

        # 2. Instagram
        video_url = task.get("public_url")
        if video_url:
            try:
                upload_to_instagram(video_url, caption)
            except Exception as e:
                print(f"⚠️ Meta IG Error: {e}")
        else:
            print("⏭️ Skipping Instagram: No public_url found in task data.")

    print("---------------------------------------")
    print("📝 Logging details...")
    latest_task = db.collection.find_one(
        {"status": "uploaded"}, sort=[("uploaded_at", -1)]
    )
    if latest_task:
        log_entry = {
            "video_name": latest_task.get("title"),
            "youtube_id": latest_task.get("youtube_id"),
            "time_slot": slot_name,
            "mode": "manual" if is_manual else "automatic",
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        log_file = "production_log.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
            print(f"✅ Log saved to: {log_file}")
        except Exception as e:
            print(f"⚠️ Failed to write log: {e}")

    print(
        f"\n✅ PIPELINE COMPLETE for {slot_name} at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return True


# ─────────────────────────────────────────────────────────────
# MANUAL PIPELINE
# ─────────────────────────────────────────────────────────────


def _run_manual_topic_entry(scraper, db, slot_name):
    topic = input("📝 Enter the Topic/Title: ").strip()
    if not topic:
        return None
    content = input("📄 Enter a brief description or idea: ").strip()

    feedback = ""
    for attempt in range(1, 11):
        print(f"\n🧠 AI is refining your idea (Attempt {attempt}/10)...")
        refined_content = scraper.refine_user_idea(topic, content, feedback)

        print(
            f"\n{'━' * 48}\n  ✨ AI REFINED YOUR IDEA\n{'━' * 48}\n{refined_content}\n{'━' * 48}"
        )
        is_correct = _prompt(
            "\n👉 Is this good?  [Y] Yes  |  [N] No, add changes -> ", default="y"
        ).lower()

        if is_correct in ("y", "yes", ""):
            db.add_task(
                title=topic,
                content=refined_content,
                source="manual",
                status="pending",
                extra_data={
                    "niche": "general",
                    "niche_slot": slot_name,
                    "source_url": "Manual Input",
                    "target_language": "English",
                },
            )
            return db.collection.find_one({"title": topic, "status": "pending"})
        else:
            print("💬 What should the AI change or improve?")
            feedback = _prompt("   → ", default="").strip()

    return None


def run_creation_pipeline(slot_name, is_manual=False):
    print(
        f"\n🎬 STARTING PRODUCTION PIPELINE: {slot_name.upper()} at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    scraper = NewsScraper()
    brain = ScriptGenerator()
    db = DBManager()
    task = None

    print("---------------------------------------")
    if is_manual:
        print(f"\n{'━' * 48}\n  📋  TOPIC SOURCE\n{'━' * 48}")
        print("\n    [A]  AI picks\n    [M]  My topic\n")
        source_choice = _prompt("   → ", default="A").upper()

        if source_choice in ("M", "MY", "MINE"):
            task = _run_manual_topic_entry(scraper, db, slot_name)
        else:
            task = run_topic_approval(scraper, slot_name)
    else:
        task = run_topic_approval(scraper, slot_name)

    if not task:
        return

    print("---------------------------------------")
    if not run_script_approval(brain, task):
        _delete_task(db, task["_id"], "User aborted script approval")
        return

    task = db.collection.find_one({"_id": task["_id"]})
    _run_post_script_steps(task, db, slot_name, is_manual=is_manual)


# ─────────────────────────────────────────────────────────────
# AUTOMATIC PIPELINE
# ─────────────────────────────────────────────────────────────


def run_automatic_pipeline(slot_name):
    MAX_AUTO_RETRIES = 3
    print(
        f"\n🤖 AUTOMATIC PIPELINE STARTING: {slot_name.upper()} at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    db = DBManager()
    scraper = NewsScraper()
    brain = ScriptGenerator()

    task = None
    for attempt in range(1, MAX_AUTO_RETRIES + 1):
        print(
            f"\n{'━' * 48}\n  🔁 SCRAPE + SCRIPT  — Attempt {attempt}/{MAX_AUTO_RETRIES}\n{'━' * 48}"
        )

        print("---------------------------------------")
        print("🔍 Auto-fetching viral topic...")
        try:
            scraper.scrape_targeted_niche(forced_slot=slot_name)
        except Exception as e:
            print(f"❌ Scraper crashed: {e}")
            continue

        pending_task = db.collection.find_one({"status": "pending"})
        if not pending_task:
            continue

        print("---------------------------------------")
        print("🧠 Auto-generating script...")
        try:
            brain.generate_script()
        except Exception as e:
            print(f"❌ Brain crashed: {e}")
            _delete_task(db, pending_task["_id"], f"Brain error: {e}")
            continue

        scripted_task = db.collection.find_one(
            {"_id": pending_task["_id"], "status": "scripted"}
        )
        if scripted_task:
            task = scripted_task
            print(f"✅ Script ready for: \"{task.get('title', '')[:60]}\"")
            break
        else:
            _delete_task(db, pending_task["_id"], "Script validation failed")

    if not task:
        print("🚨 Could not produce a valid script after all retries. Aborting.")
        return

    _run_post_script_steps(task, db, slot_name, is_manual=False)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 51)
    print("  🤖 YOUTUBE AUTOMATION PIPELINE STARTING")
    print("=" * 51)

    # 🟢 NEW: Run the Pre-Flight Auth Check
    auth_valid = verify_and_refresh_token()
    if not auth_valid:
        print("🚨 Shutting down pipeline due to invalid YouTube credentials.")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("slot", nargs="?", default=None)
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()

    target_slot = args.slot
    if not target_slot:
        h = datetime.datetime.now().hour
        if 0 <= h < 4:
            target_slot = "mid_night"
        elif 4 <= h < 8:
            target_slot = "4_am"
        elif 8 <= h < 12:
            target_slot = "8_am"
        elif 12 <= h < 16:
            target_slot = "mid_day"
        elif 16 <= h < 20:
            target_slot = "4_pm"
        else:
            target_slot = "8_pm"

    if args.manual:
        run_creation_pipeline(target_slot, is_manual=True)
    elif args.auto:
        run_automatic_pipeline(target_slot)
    else:
        is_manual = _ask_mode()
        if is_manual:
            run_creation_pipeline(target_slot, is_manual=True)
        else:
            run_automatic_pipeline(target_slot)
