import sys
import asyncio
import argparse
import json
import os
import glob
import shutil
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

MODE_TIMEOUT = 60  # Timer for Manual/Automatic selection screen only
PROMPT_TIMEOUT = 300  # Timer for all other interactive prompts


# ─────────────────────────────────────────────────────────────
# TIMED INPUT — MODE SELECTION (60s, no keystroke cancel)
# ─────────────────────────────────────────────────────────────


def _timed_input(prompt, timeout=MODE_TIMEOUT, default=""):
    """
    Simple timed input used ONLY for the startup mode selection screen.
    Countdown ticker shown. Auto-selects default after `timeout` seconds.
    Typing does NOT cancel the timer — Enter submits the answer as normal.
    Works on both Windows and Unix.
    """
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


# ─────────────────────────────────────────────────────────────
# TIMED INPUT — ALL OTHER PROMPTS (300s, keystroke cancels timer)
# ─────────────────────────────────────────────────────────────


def _prompt(prompt_text, timeout=PROMPT_TIMEOUT, default=""):
    """
    Timed input used for ALL prompts EXCEPT mode selection.

    Behaviour:
      - Shows a 300s countdown ticker.
      - As soon as the user types ANY character, the timer is cancelled
        and the prompt waits indefinitely for them to finish and press Enter.
      - If nobody types anything within `timeout` seconds, auto-selects `default`.

    Works on Windows (msvcrt) and Unix (termios/select).
    """
    import sys

    # ── Windows implementation ──────────────────────────────
    if sys.platform == "win32":
        import msvcrt

        result = [default]
        timer_cancelled = threading.Event()
        input_done = threading.Event()
        typed_chars = []

        def _watch_keys():
            """Background thread: watches for first keypress to cancel timer."""
            while not input_done.is_set():
                if msvcrt.kbhit():
                    timer_cancelled.set()
                    break
                input_done.wait(timeout=0.05)

        watcher = threading.Thread(target=_watch_keys, daemon=True)
        watcher.start()

        # Countdown — stops as soon as timer_cancelled or input_done fires
        for remaining in range(timeout, 0, -1):
            if timer_cancelled.is_set() or input_done.is_set():
                break
            print(
                f"\r   ⏳ Auto-selecting in {remaining:3d}s... (start typing to cancel timer)",
                end="",
                flush=True,
            )
            timer_cancelled.wait(timeout=1)

        if not timer_cancelled.is_set() and not input_done.is_set():
            # Nobody typed — auto-select
            print(f"\r   ⏰ Time's up! Auto-selecting default: '{default}'{' ' * 30}")
            input_done.set()
            return default

        # Timer cancelled by keystroke — clear the ticker line and wait for full input
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

        # ── Unix implementation ─────────────────────────────────
    else:
        import select
        import termios
        import tty

        result = [default]
        timer_cancelled = threading.Event()
        input_done = threading.Event()

        def _watch_stdin():
            """Background thread: checks if stdin has data (i.e. user typed something)."""
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

        # Countdown
        for remaining in range(timeout, 0, -1):
            if timer_cancelled.is_set() or input_done.is_set():
                break
            print(
                f"\r   ⏳ Auto-selecting in {remaining:3d}s... (start typing to cancel timer)",
                end="",
                flush=True,
            )
            timer_cancelled.wait(timeout=1)

        if not timer_cancelled.is_set() and not input_done.is_set():
            print(f"\r   ⏰ Time's up! Auto-selecting default: '{default}'{' ' * 30}")
            input_done.set()
            return default

        # Timer cancelled — clear ticker and wait for full input
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


# ─────────────────────────────────────────────────────────────
# STARTUP MODE SELECTOR
# ─────────────────────────────────────────────────────────────


def _ask_mode():
    """
    Asks the user to choose Manual or Automatic mode at startup.
    Defaults to Automatic after 60 seconds of no input.
    Returns True if manual, False if automatic.
    """
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
    """Hard-delete a task from the DB by its _id."""
    try:
        db.collection.delete_one({"_id": task_id})
        print(f"🗑️  Task deleted from DB. Reason: {reason}")
    except Exception as e:
        print(f"⚠️  Could not delete task from DB: {e}")


def _cleanup_task_files(task):
    """Delete any files already written to the task's folder."""
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
    """Pretty-print the 3 topic choices for the user."""
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
    """Pretty-print the generated script scenes for user review."""
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
# TOPIC APPROVAL LOOP  (max 5 regenerations, 60s timer)
# ─────────────────────────────────────────────────────────────


def run_topic_approval(scraper, slot_name):
    """
    Fetches 3 viral topics and asks the user to pick one.
    - 60s timer: auto-selects option [1] on timeout.
    - R to regenerate (up to 5 times), Q to quit.
    Returns the saved task dict on success, or None if aborted.
    """
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
                    f"\n⚠️  Max regenerations ({MAX_REGEN}) reached. Auto-selecting option [1]."
                )
                choice = "1"
            else:
                remaining = MAX_REGEN - regen_count
                print(
                    f"🔄 Regenerating... ({remaining} regeneration{'s' if remaining != 1 else ''} left)"
                )
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


# ─────────────────────────────────────────────────────────────
# SCRIPT APPROVAL LOOP  (max 5 regenerations, 60s timer)
# ─────────────────────────────────────────────────────────────


def run_script_approval(brain, task):
    """
    Generates a script and shows it to the user for review.
    - A: approve, R: regenerate, N: regenerate with notes, Q: quit.
    - 60s timer: auto-approves on timeout.
    Max 5 regenerations before auto-approving.
    Returns True on approval, False if aborted.
    """
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
                    print("🚨 Max retries reached. Aborting.")
                    return False
                continue

        _display_script(data)

        print(
            "👉 A to approve  |  R to regenerate  |  N to regenerate with notes  |  Q to quit"
        )
        choice = _prompt("   → ", default="A").upper()

        if choice == "Q":
            print("⛔ Script approval aborted by user.")
            return False

        elif choice == "A":
            print("\n✅ Script approved! Saving to database...")
            brain.approve_and_save(task, data)
            return True

        elif choice == "R":
            regen_count += 1
            feedback = ""
            if regen_count >= MAX_REGEN:
                print(
                    f"\n⚠️  Max regenerations ({MAX_REGEN}) reached. Auto-approving current script."
                )
                brain.approve_and_save(task, data)
                return True
            remaining = MAX_REGEN - regen_count
            print(
                f"🔄 Regenerating... ({remaining} attempt{'s' if remaining != 1 else ''} left)"
            )
            data = None

        elif choice == "N":
            regen_count += 1
            if regen_count >= MAX_REGEN:
                print(
                    f"\n⚠️  Max regenerations ({MAX_REGEN}) reached. Auto-approving current script."
                )
                brain.approve_and_save(task, data)
                return True
            print("💬 What should the AI change or improve?")
            feedback = _prompt("   → ", default="").strip()
            if not feedback:
                print("⚠️ No feedback entered. Regenerating without notes...")
            remaining = MAX_REGEN - regen_count
            print(
                f"🔄 Regenerating with your notes... ({remaining} attempt{'s' if remaining != 1 else ''} left)"
            )
            data = None

        else:
            print("⚠️ Invalid input. Please enter A, R, N, or Q.")


# ─────────────────────────────────────────────────────────────
# SHARED POST-SCRIPT PIPELINE (Steps 3–9)
# Used by BOTH manual and automatic pipelines after script is ready.
# ─────────────────────────────────────────────────────────────


def _run_post_script_steps(task, db, slot_name, is_manual):
    """
    Runs Steps 3–9 (Voice → Visuals → Assemble → Upload → Log → Cleanup).
    On any failure: deletes DB record + task folder, then aborts.
    Returns True on full success, False on any failure.
    """

    # ── STEP 3: VOICE ────────────────────────────
    print("---------------------------------------")
    try:
        voice = VoiceEngine()
        asyncio.run(voice.generate_audio())
        if not db.collection.find_one({"_id": task["_id"], "status": "voiced"}):
            raise RuntimeError("Voice generation did not update status to 'voiced'.")
    except Exception as e:
        print(f"❌ Voice generation failed: {e}")
        _delete_task(db, task["_id"], reason=f"Voice error: {e}")
        _cleanup_task_files(task)
        return False

    # ── STEP 4: VISUALS ──────────────────────────
    print("---------------------------------------")
    try:
        visuals = VisualScout()
        visuals.download_visuals()
        if not db.collection.find_one(
            {"_id": task["_id"], "status": "ready_to_assemble"}
        ):
            raise RuntimeError("Visuals did not update status to 'ready_to_assemble'.")
    except Exception as e:
        print(f"❌ Visuals download failed: {e}")
        _delete_task(db, task["_id"], reason=f"Visuals error: {e}")
        _cleanup_task_files(task)
        return False

    # ── STEP 5: ASSEMBLER ────────────────────────
    print("---------------------------------------")
    try:
        assembler = VideoAssembler()
        assembler.assemble()
        if not db.collection.find_one(
            {"_id": task["_id"], "status": "ready_to_upload"}
        ):
            raise RuntimeError("Assembler did not update status to 'ready_to_upload'.")
    except Exception as e:
        print(f"❌ Video assembly failed: {e}")
        _delete_task(db, task["_id"], reason=f"Assembler error: {e}")
        _cleanup_task_files(task)
        return False

    # ── STEP 6: UPLOAD PREP ──────────────────────
    print("---------------------------------------")
    try:
        prep = UploadManager()
        prep.prepare_package()
    except Exception as e:
        print(f"❌ Upload prep failed: {e}")
        _delete_task(db, task["_id"], reason=f"Upload prep error: {e}")
        _cleanup_task_files(task)
        return False

    # ── STEP 7: YOUTUBE UPLOAD ───────────────────
    print("---------------------------------------")
    try:
        uploader = YouTubeUploader()
        uploader.upload_video()
    except Exception as e:
        print(f"❌ YouTube upload failed: {e}")
        # _delete_task(db, task["_id"], reason=f"Upload error: {e}")
        # _cleanup_task_files(task)
        return False

    # ── STEP 8: JSON LOGGING ─────────────────────
    print("---------------------------------------")
    print("📝 Logging details to JSON...")
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
        log_file = "production_log.json"
        logs = []
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except:
                logs = []
        logs.append(log_entry)
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=4)
        print(f"✅ Log saved to: {log_file}")
    else:
        print("⚠️ Log skipped (No upload confirmed).")

    # ── STEP 9: CLEANUP ──────────────────────────
    print("---------------------------------------")
    print("🧹 Cleaning up temporary metadata files...")
    for f in glob.glob("metadata_*.txt"):
        try:
            os.remove(f)
            print(f"   🗑️ Deleted: {f}")
        except:
            pass

    # Get the current date and time as a datetime object
    end_time = datetime.datetime.now()

    # Format the datetime object into a string in "YYYY-MM-DD HH:MM:SS" format
    formatted_end_time = end_time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n✅ PIPELINE COMPLETE for {slot_name} at {formatted_end_time}.")
    return True


# ─────────────────────────────────────────────────────────────
# MANUAL PIPELINE
# ─────────────────────────────────────────────────────────────


def _run_manual_topic_entry(scraper, db, slot_name):
    """
    Handles the 'I have my own topic' path in manual mode.
    User types a topic + content, AI refines it, user approves or gives feedback.
    Returns the saved task dict on success, or None on failure/abort.
    """
    topic = input("📝 Enter the Topic/Title: ").strip()
    if not topic:
        print("⚠️  No topic entered. Aborting.")
        return None

    content = input("📄 Enter a brief description or idea: ").strip()

    feedback = ""
    for attempt in range(1, 11):
        print(f"\n🧠 AI is refining your idea (Attempt {attempt}/10)...")
        try:
            refined_content = scraper.refine_user_idea(topic, content, feedback)
        except Exception as e:
            print(f"❌ Idea refinement crashed: {e}. Retrying...")
            continue

        print(f"\n{'━' * 48}")
        print("  ✨ AI REFINED YOUR IDEA")
        print(f"{'━' * 48}")
        print(refined_content)
        print(f"{'━' * 48}")

        print("\n👉 Is this good?  [Y] Yes, continue  |  [N] No, I want changes")
        is_correct = _prompt("   → ", default="y").lower()

        if is_correct in ("y", "yes", ""):
            try:
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
                task = db.collection.find_one({"title": topic, "status": "pending"})
                if not task:
                    raise RuntimeError("Task was not saved to DB.")
                print("💾 Topic saved to database!")
                return task
            except Exception as e:
                print(f"❌ Failed to save task: {e}. Retrying...")
                continue
        else:
            print("💬 What should the AI change or improve?")
            feedback = _prompt("   → ", default="").strip()
            if not feedback:
                print("⚠️  No feedback entered. Regenerating as-is...")

    print("\n❌ Max attempts (10) reached. Please try a different topic.")
    return None


def run_creation_pipeline(slot_name, is_manual=False):
    """
    Manual/interactive pipeline.

    In manual mode, the user is first asked:
      'Do you have a topic, or should AI pick one?'
      - AI picks  → runs the interactive RSS topic approval flow
      - Own topic → user types topic + content, AI refines it, user approves

    Error handling rules:
      - Error in STEP 1 (topic) or STEP 2 (script):
            → Delete the DB record and abort gracefully.
      - Error in STEP 3+ (voice, visuals, assembler, upload):
            → Delete the DB record + task folder, then abort.
    """

    # Get the current date and time as a datetime object
    start_time = datetime.datetime.now()

    # Format the datetime object into a string in "YYYY-MM-DD HH:MM:SS" format
    formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"\n🎬 STARTING PRODUCTION PIPELINE: {slot_name.upper()} at {formatted_start_time}"
    )
    print(f"   Mode: {'🛠️  MANUAL' if is_manual else '🤖  INTERACTIVE'}")

    scraper = NewsScraper()
    brain = ScriptGenerator()
    db = DBManager()
    task = None

    # ── STEP 1: Topic Selection ──────────────────
    print("---------------------------------------")

    if is_manual:
        # ── Ask user: own topic or AI picks? ────
        print(f"\n{'━' * 48}")
        print("  📋  TOPIC SOURCE")
        print(f"{'━' * 48}")
        print("\n  Do you have a topic in mind, or should AI find one?\n")
        print("    [A]  AI picks  — AI searches for a viral topic for you")
        print("    [M]  My topic  — You provide the topic and idea")
        print(f"\n  Default: AI picks (after {PROMPT_TIMEOUT} seconds)\n")

        source_choice = _prompt("   → ", default="A").upper()

        if source_choice in ("M", "MY", "MINE", "MY TOPIC"):
            # ── User provides their own topic ──
            print("\n🛠️  Got it! Let's build your topic.\n")
            try:
                task = _run_manual_topic_entry(scraper, db, slot_name)
            except Exception as e:
                print(f"❌ Topic entry crashed: {e}")
                return
            if not task:
                return

        else:
            # ── AI picks the topic (same interactive RSS flow) ──
            print("\n🤖  AI is searching for a viral topic...\n")
            try:
                task = run_topic_approval(scraper, slot_name)
            except Exception as e:
                print(f"❌ Topic selection crashed: {e}")
                return
            if not task:
                return

    else:
        # ── Non-manual interactive flow (same as before) ──
        print("🤖 Running Interactive AI Scraper Flow...")
        try:
            task = run_topic_approval(scraper, slot_name)
        except Exception as e:
            print(f"❌ Topic selection crashed: {e}")
            return
        if not task:
            return

    # ── STEP 2: Script Approval ──────────────────
    print("---------------------------------------")
    try:
        script_ok = run_script_approval(brain, task)
    except Exception as e:
        print(f"❌ Script approval crashed: {e}")
        _delete_task(db, task["_id"], reason=f"Script error: {e}")
        return

    if not script_ok:
        _delete_task(db, task["_id"], reason="User aborted script approval")
        return

    # Refresh task from DB after script was saved
    task = db.collection.find_one({"_id": task["_id"]})
    if not task:
        print("❌ Could not reload task from DB after script approval. Aborting.")
        return

    # ── STEPS 3–9: Shared post-script pipeline ──
    _run_post_script_steps(task, db, slot_name, is_manual=True)


# ─────────────────────────────────────────────────────────────
# AUTOMATIC PIPELINE (fully hands-free, no approvals)
# ─────────────────────────────────────────────────────────────


def run_automatic_pipeline(slot_name):
    """
    Fully automatic end-to-end pipeline.

    Error handling rules:
      - Error in STEP 1 (scraper) or STEP 2 (brain):
            → Delete the DB record and RESTART from Step 1 (up to MAX_AUTO_RETRIES).
      - Error in STEP 3+ (voice, visuals, assembler, upload):
            → Delete the DB record + task folder, then ABORT.
    """
    MAX_AUTO_RETRIES = 3
    # Get the current date and time as a datetime object
    start_time = datetime.datetime.now()

    # Format the datetime object into a string in "YYYY-MM-DD HH:MM:SS" format
    formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"\n🤖 AUTOMATIC PIPELINE STARTING: {slot_name.upper()} at {formatted_start_time}."
    )

    db = DBManager()
    scraper = NewsScraper()
    brain = ScriptGenerator()

    # ════════════════════════════════════════════════════════
    # PHASE 1: SCRAPE + SCRIPT  (retry loop)
    # ════════════════════════════════════════════════════════
    task = None
    for attempt in range(1, MAX_AUTO_RETRIES + 1):
        print(f"\n{'━' * 48}")
        print(f"  🔁 SCRAPE + SCRIPT  — Attempt {attempt}/{MAX_AUTO_RETRIES}")
        print(f"{'━' * 48}")

        # ── STEP 1: Auto-scrape topic ──
        print("---------------------------------------")
        print("🔍 Auto-fetching viral topic...")
        try:
            scraper.scrape_targeted_niche(forced_slot=slot_name)
        except Exception as e:
            print(f"❌ Scraper crashed: {e}")
            if attempt < MAX_AUTO_RETRIES:
                print(f"🔄 Retrying scraper... ({MAX_AUTO_RETRIES - attempt} left)")
                continue
            else:
                print("🚨 Max retries reached on scraper. Aborting pipeline.")
                return

        pending_task = db.collection.find_one({"status": "pending"})
        if not pending_task:
            print("❌ No pending task found after scraping.")
            if attempt < MAX_AUTO_RETRIES:
                print(f"🔄 Retrying scraper... ({MAX_AUTO_RETRIES - attempt} left)")
                continue
            else:
                print("🚨 Max retries reached. Aborting pipeline.")
                return

        # ── STEP 2: Auto-generate script ──
        print("---------------------------------------")
        print("🧠 Auto-generating script...")
        try:
            brain.generate_script()
        except Exception as e:
            print(f"❌ Brain crashed: {e}")
            _delete_task(db, pending_task["_id"], reason=f"Brain error: {e}")
            if attempt < MAX_AUTO_RETRIES:
                print(
                    f"🔄 Restarting from scraper... ({MAX_AUTO_RETRIES - attempt} left)"
                )
                continue
            else:
                print("🚨 Max retries reached on brain. Aborting pipeline.")
                return

        scripted_task = db.collection.find_one(
            {"_id": pending_task["_id"], "status": "scripted"}
        )
        if not scripted_task:
            print(
                "❌ Script generation failed silently (task not marked as 'scripted')."
            )
            _delete_task(db, pending_task["_id"], reason="Script validation failed")
            if attempt < MAX_AUTO_RETRIES:
                print(
                    f"🔄 Restarting from scraper... ({MAX_AUTO_RETRIES - attempt} left)"
                )
                continue
            else:
                print("🚨 Max retries reached. Aborting pipeline.")
                return

        task = scripted_task
        print(f"✅ Script ready for: \"{task.get('title', '')[:60]}\"")
        break

    if not task:
        print("🚨 Could not produce a valid script after all retries. Aborting.")
        return

    # ════════════════════════════════════════════════════════
    # PHASE 2: VOICE → VISUALS → ASSEMBLE → UPLOAD (Steps 3–9)
    # ════════════════════════════════════════════════════════
    _run_post_script_steps(task, db, slot_name, is_manual=False)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "slot",
        nargs="?",
        help="Time slot (mid_night/4_am/8_am/mid_day/4_pm/8_pm)",
        default=None,
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Skip the mode prompt and force manual mode",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Skip the mode prompt and force automatic mode",
    )
    args = parser.parse_args()

    # ── Auto-detect time slot ──
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

    # ── Mode selection ──
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
