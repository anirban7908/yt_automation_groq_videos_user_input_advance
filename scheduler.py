import schedule
import time
import subprocess
import datetime
import sys
import os
import json
import threading
import logging
import platform
import signal
from auth_check import verify_and_refresh_token

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

PYTHON_EXEC = sys.executable
MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
LOG_DIR = "logs"
SCHEDULER_LOG = os.path.join(LOG_DIR, "scheduler.log")
JOB_HISTORY = os.path.join(LOG_DIR, "job_history.json")

# Max time a single pipeline run is allowed before being killed
JOB_TIMEOUT = 3.5 * 60 * 60  # 3.5 hours

os.makedirs(LOG_DIR, exist_ok=True)

# 🟢 UNIFIED CONFIG: Single source of truth for both scheduler and recovery logic
SCHEDULE_CONFIG = {
    "mid_night": "00:00",
    "4_am": "04:00",
    "8_am": "08:00",
    "mid_day": "12:00",
    "4_pm": "16:00",
    "8_pm": "20:00",
}

# ─────────────────────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(SCHEDULER_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scheduler")

# ─────────────────────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────

_job_lock = threading.Lock()
_active_job = {"slot": None, "started_at": None}


def _is_job_running():
    with _job_lock:
        return _active_job["slot"] is not None


def _set_job_running(slot):
    with _job_lock:
        _active_job["slot"] = slot
        _active_job["started_at"] = datetime.datetime.now()


def _clear_job():
    with _job_lock:
        _active_job["slot"] = None
        _active_job["started_at"] = None


def _log_job_result(slot, status, duration_sec, error=None):
    """Append a job result entry safely."""
    entry = {
        "slot": slot,
        "status": status,
        "duration_sec": round(duration_sec, 1),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": error,
    }

    with _job_lock:
        history = []
        if os.path.exists(JOB_HISTORY):
            try:
                with open(JOB_HISTORY, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append(entry)
        history = history[-200:]

        with open(JOB_HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)


def _kill_process_tree(process):
    """Cross-platform kill switch to destroy Python AND FFmpeg children."""
    try:
        if platform.system() == "Windows":
            process.send_signal(signal.CTRL_BREAK_EVENT)
            time.sleep(1)
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception as e:
        log.error(f"Failed to cleanly kill process tree: {e}")


# ─────────────────────────────────────────────────────────────
# CORE JOB RUNNER
# ─────────────────────────────────────────────────────────────


def job(slot):
    if _is_job_running():
        running_slot = _active_job["slot"]
        elapsed = int(
            (datetime.datetime.now() - _active_job["started_at"]).total_seconds() / 60
        )
        log.warning(
            f"⏭️  SKIPPING [{slot.upper()}] — [{running_slot.upper()}] still running ({elapsed}m)"
        )
        _log_job_result(
            slot, "skipped", 0, error=f"Job '{running_slot}' running ({elapsed}m)"
        )
        return

    _set_job_running(slot)
    start_time = datetime.datetime.now()
    log.info(f"🔔 STARTING JOB: [{slot.upper()}]")

    try:
        custom_env = os.environ.copy()
        custom_env["PYTHONIOENCODING"] = "utf-8"

        slot_log_path = os.path.join(
            LOG_DIR, f"run_{slot}_{start_time.strftime('%Y%m%d_%H%M%S')}.log"
        )
        log.info(f"   📝 Real-time logs available at: {slot_log_path}")

        # 🟢 THE FIX: Setup Process Groups & Route stdout directly to disk
        kwargs = {}
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["preexec_fn"] = os.setsid

        with open(slot_log_path, "w", encoding="utf-8") as slot_log:
            process = subprocess.Popen(
                [PYTHON_EXEC, MAIN_SCRIPT, slot, "--auto"],
                stdout=slot_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=custom_env,
                **kwargs,
            )

            timed_out = False
            try:
                # Wait for process to finish naturally while output streams to disk
                process.wait(timeout=JOB_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_tree(process)
                process.communicate()  # Reap the zombie
                log.error(
                    f"⏰ TIMEOUT: [{slot.upper()}] exceeded {JOB_TIMEOUT/3600:.1f}h limit. Process tree killed."
                )

        duration = (datetime.datetime.now() - start_time).total_seconds()

        if timed_out:
            _log_job_result(
                slot, "timeout", duration, error=f"Exceeded {JOB_TIMEOUT}s timeout"
            )
        elif process.returncode == 0:
            _log_job_result(slot, "success", duration)
            log.info(f"✅ [{slot.upper()}] FINISHED SUCCESSFULLY in {duration/60:.1f}m")
        else:
            _log_job_result(
                slot, "failed", duration, error=f"Exit code {process.returncode}"
            )
            log.error(
                f"❌ [{slot.upper()}] FAILED (code {process.returncode}) after {duration/60:.1f}m"
            )

    except Exception as e:
        duration = (datetime.datetime.now() - start_time).total_seconds()
        _log_job_result(slot, "failed", duration, error=str(e))
        log.exception(f"❌ [{slot.upper()}] CRASHED: {e}")
    finally:
        _clear_job()


# ─────────────────────────────────────────────────────────────
# MISSED JOB RECOVERY
# ─────────────────────────────────────────────────────────────


def _check_missed_jobs():
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    ran_today = set()
    with _job_lock:
        if os.path.exists(JOB_HISTORY):
            try:
                with open(JOB_HISTORY, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in history:
                    if entry.get("timestamp", "").startswith(today) and entry.get(
                        "status"
                    ) in ("success", "failed"):
                        ran_today.add(entry["slot"])
            except Exception:
                pass

    missed = []
    # 🟢 THE FIX: Dynamically check against the actual active schedule configuration
    for slot, time_str in SCHEDULE_CONFIG.items():
        hour, minute = map(int, time_str.split(":"))
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if slot_time <= now and slot not in ran_today:
            missed.append((slot, slot_time))

    if not missed:
        log.info("✅ No missed jobs detected on startup.")
        return

    missed.sort(key=lambda x: x[1], reverse=True)
    most_recent_slot, missed_time = missed[0]
    minutes_ago = int((now - missed_time).total_seconds() / 60)

    log.warning(
        f"⚠️  MISSED JOB DETECTED: [{most_recent_slot.upper()}] (due {minutes_ago}m ago). Running now..."
    )
    threading.Thread(target=job, args=(most_recent_slot,), daemon=True).start()


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 51)
    log.info("  🤖 YOUTUBE AUTOMATION SCHEDULER STARTED")
    log.info(f"  📅 {len(SCHEDULE_CONFIG)} slots/day — fully automatic")
    log.info(f"  ⏰ Max job duration: {JOB_TIMEOUT/3600:.1f} hours")
    log.info("  🛑 Press Ctrl+C to stop")
    log.info("=" * 51)

    # 🟢 NEW: Run the Pre-Flight Auth Check
    auth_valid = verify_and_refresh_token()
    if not auth_valid:
        log.error("🚨 Shutting down scheduler due to invalid YouTube credentials.")
        sys.exit(1)

    _check_missed_jobs()

    # Dynamically bind schedule based on config
    for slot, time_str in SCHEDULE_CONFIG.items():
        schedule.every().day.at(time_str).do(job, slot=slot)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("🛑 Scheduler stopped by user.")
