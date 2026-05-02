import schedule
import time
import subprocess
import datetime
import sys
import os
import json
import threading
import logging

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

PYTHON_EXEC = sys.executable
MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
LOG_DIR = "logs"
SCHEDULER_LOG = os.path.join(LOG_DIR, "scheduler.log")
JOB_HISTORY = os.path.join(LOG_DIR, "job_history.json")

# Max time a single pipeline run is allowed before being killed (seconds)
JOB_TIMEOUT = 3.5 * 60 * 60  # 3.5 hours

os.makedirs(LOG_DIR, exist_ok=True)

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
# OVERLAP GUARD
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


# ─────────────────────────────────────────────────────────────
# JOB HISTORY LOGGER
# ─────────────────────────────────────────────────────────────


def _log_job_result(slot, status, duration_sec, error=None):
    """Append a job result entry to logs/job_history.json."""
    entry = {
        "slot": slot,
        "status": status,  # "success" | "failed" | "skipped" | "timeout"
        "duration_sec": round(duration_sec, 1),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": error,
    }

    history = []
    if os.path.exists(JOB_HISTORY):
        try:
            with open(JOB_HISTORY, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    history.append(entry)
    history = history[-200:]  # Keep last 200 entries only

    with open(JOB_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


# ─────────────────────────────────────────────────────────────
# CORE JOB RUNNER
# ─────────────────────────────────────────────────────────────


def job(slot):
    """
    Runs the full automatic pipeline for the given time slot.

    Safeguards:
      - Overlap prevention: skips if a job is already running
      - Timeout: kills the process if it exceeds JOB_TIMEOUT seconds
      - Logging: records every run to logs/job_history.json + per-run log file
      - --auto flag: ensures main.py never waits for interactive input
    """

    # ── Overlap guard ──
    if _is_job_running():
        running_slot = _active_job["slot"]
        running_since = _active_job["started_at"]
        elapsed_minutes = int(
            (datetime.datetime.now() - running_since).total_seconds() / 60
        )
        log.warning(
            f"⏭️  SKIPPING [{slot.upper()}] — "
            f"[{running_slot.upper()}] still running ({elapsed_minutes}m elapsed)"
        )
        _log_job_result(
            slot,
            "skipped",
            0,
            error=f"Job '{running_slot}' still running ({elapsed_minutes}m)",
        )
        return

    _set_job_running(slot)
    start_time = datetime.datetime.now()
    log.info(f"🔔 STARTING JOB: [{slot.upper()}]")

    try:
        # Force the subprocess to use UTF-8 encoding so emojis don't crash Windows
        custom_env = os.environ.copy()
        custom_env["PYTHONIOENCODING"] = "utf-8"

        # ── Run main.py with --auto flag (fully hands-free) ──
        process = subprocess.Popen(
            [PYTHON_EXEC, MAIN_SCRIPT, slot, "--auto"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=custom_env,  # 👈 Pass the custom environment here
        )

        # ── Per-run log file ──
        slot_log_path = os.path.join(
            LOG_DIR, f"run_{slot}_{start_time.strftime('%Y%m%d_%H%M%S')}.log"
        )
        timed_out = False

        with open(slot_log_path, "w", encoding="utf-8") as slot_log:
            try:
                stdout, _ = process.communicate(timeout=JOB_TIMEOUT)
                for line in stdout.splitlines():
                    print(f"  {line}")
                    slot_log.write(line + "\n")
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.communicate()
                log.error(
                    f"⏰ TIMEOUT: [{slot.upper()}] exceeded "
                    f"{JOB_TIMEOUT/3600:.1f}h limit. Process killed."
                )

        duration = (datetime.datetime.now() - start_time).total_seconds()

        if timed_out:
            _log_job_result(
                slot, "timeout", duration, error=f"Exceeded {JOB_TIMEOUT}s timeout"
            )
            log.error(f"❌ [{slot.upper()}] TIMED OUT after {duration/60:.1f}m")

        elif process.returncode == 0:
            _log_job_result(slot, "success", duration)
            log.info(f"✅ [{slot.upper()}] FINISHED SUCCESSFULLY in {duration/60:.1f}m")

        else:
            _log_job_result(
                slot, "failed", duration, error=f"Exit code {process.returncode}"
            )
            log.error(
                f"❌ [{slot.upper()}] FAILED (exit code {process.returncode}) "
                f"after {duration/60:.1f}m — see {slot_log_path}"
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

SLOT_HOURS = {
    "mid_night": 0,
    "4_am": 4,
    "8_am": 8,
    "mid_day": 12,
    "4_pm": 16,
    "8_pm": 20,
}


def _check_missed_jobs():
    """
    On startup, checks if any slot was missed today.
    If the machine was off or scheduler wasn't running, runs the most
    recently missed slot immediately in a background thread.
    Only recovers the single most recent missed slot to avoid flooding.
    """
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Load today's completed runs
    ran_today = set()
    if os.path.exists(JOB_HISTORY):
        try:
            with open(JOB_HISTORY, "r", encoding="utf-8") as f:
                history = json.load(f)
            for entry in history:
                if entry.get("timestamp", "").startswith(today):
                    if entry.get("status") in ("success", "failed"):
                        ran_today.add(entry["slot"])
        except Exception:
            pass

    # Find slots due today that haven't run yet
    missed = []
    for slot, hour in SLOT_HOURS.items():
        slot_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot_time <= now and slot not in ran_today:
            missed.append((slot, slot_time))

    if not missed:
        log.info("✅ No missed jobs detected on startup.")
        return

    # Run only the most recently missed slot
    missed.sort(key=lambda x: x[1], reverse=True)
    most_recent_slot, missed_time = missed[0]
    minutes_ago = int((now - missed_time).total_seconds() / 60)

    log.warning(
        f"⚠️  MISSED JOB DETECTED: [{most_recent_slot.upper()}] "
        f"(was due {minutes_ago}m ago). Running now..."
    )
    threading.Thread(target=job, args=(most_recent_slot,), daemon=True).start()


# ─────────────────────────────────────────────────────────────
# SCHEDULE DEFINITION
# ─────────────────────────────────────────────────────────────

# schedule.every().day.at("17:00").do(job, slot="mid_night")
# schedule.every().day.at("04:00").do(job, slot="4_am")
# schedule.every().day.at("08:00").do(job, slot="8_am")
# schedule.every().day.at("12:00").do(job, slot="mid_day")
# schedule.every().day.at("16:00").do(job, slot="4_pm")
# schedule.every().day.at("20:00").do(job, slot="8_pm")
schedule.every().day.at("00:00").do(job, slot="mid_night")
schedule.every().day.at("06:00").do(job, slot="4_am")
schedule.every().day.at("12:00").do(job, slot="8_am")
schedule.every().day.at("18:00").do(job, slot="mid_day")
# schedule.every().day.at("16:00").do(job, slot="4_pm")
# schedule.every().day.at("20:00").do(job, slot="8_pm")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 51)
    log.info("  🤖 YOUTUBE AUTOMATION SCHEDULER STARTED")
    log.info(f"  📅 6 slots/day — fully automatic")
    log.info(f"  ⏰ Max job duration: {JOB_TIMEOUT/3600:.1f} hours")
    log.info(f"  📝 Logs: {LOG_DIR}/scheduler.log")
    log.info(f"  📋 History: {LOG_DIR}/job_history.json")
    log.info("  🛑 Press Ctrl+C to stop")
    log.info("=" * 51)

    # Recover any missed jobs from today on startup
    _check_missed_jobs()

    # Main loop — check every 30s for ~30s accuracy (old version was 60s)
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("🛑 Scheduler stopped by user.")
