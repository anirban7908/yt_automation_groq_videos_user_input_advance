import edge_tts
import asyncio
import os
from mutagen.mp3 import MP3
from core.db_manager import DBManager

MAX_RETRIES = 3  # Max attempts per scene before skipping
RETRY_DELAY = 3  # Seconds to wait between retries
MIN_AUDIO_DURATION = 0.5  # Minimum valid audio duration in seconds

# Fallback voice used if the primary voice fails all retries
FALLBACK_VOICE = "en-US-GuyNeural"


class VoiceEngine:
    def __init__(self):
        self.db = DBManager()

    async def _generate_single_segment(self, text, voice, path, rate="+10%"):
        """
        Generates a single TTS audio segment with retry logic.
        Tries the primary voice first. If all retries fail, tries the fallback voice.
        Returns (path, duration) on success, or raises on complete failure.
        """
        last_error = None

        # ── Primary voice attempts ──
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(path)

                # Validate the file was actually written and is a real MP3
                if not os.path.exists(path) or os.path.getsize(path) < 1000:
                    raise ValueError(f"Audio file too small or missing: {path}")

                duration = MP3(path).info.length
                if duration < MIN_AUDIO_DURATION:
                    raise ValueError(f"Audio duration too short: {duration:.2f}s")

                return path, duration

            except Exception as e:
                last_error = e
                print(f"      ⚠️ Attempt {attempt}/{MAX_RETRIES} failed ({voice}): {e}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

        # ── Fallback voice attempt (only if primary voice != fallback) ──
        if voice != FALLBACK_VOICE:
            print(f"      🔄 Trying fallback voice: {FALLBACK_VOICE}...")
            try:
                communicate = edge_tts.Communicate(text, FALLBACK_VOICE, rate=rate)
                await communicate.save(path)

                if not os.path.exists(path) or os.path.getsize(path) < 1000:
                    raise ValueError("Fallback audio file too small or missing")

                duration = MP3(path).info.length
                if duration < MIN_AUDIO_DURATION:
                    raise ValueError(
                        f"Fallback audio duration too short: {duration:.2f}s"
                    )

                print(f"      ✅ Fallback voice succeeded.")
                return path, duration

            except Exception as e:
                last_error = e
                print(f"      ❌ Fallback voice also failed: {e}")

        raise RuntimeError(
            f"All voice attempts failed for scene. Last error: {last_error}"
        )

    async def generate_audio(self):
        task = self.db.collection.find_one({"status": "scripted"})
        if not task:
            return

        folder = task.get("folder_path")
        scenes = task.get("script_data", [])
        niche = task.get("niche", "general").lower()
        selected_voice = task.get("voice_model", FALLBACK_VOICE)

        print(
            f"🎙️ Generating Audio ({len(scenes)} segments) "
            f"using '{selected_voice}' for niche '{niche}'..."
        )

        updated_scenes = []
        failed_scenes = []

        for i, scene in enumerate(scenes):
            filename = f"voice_{i}.mp3"
            path = os.path.join(folder, filename)
            text = scene["text"]

            try:
                audio_path, duration = await self._generate_single_segment(
                    text, selected_voice, path
                )

                scene["audio_path"] = audio_path
                scene["duration"] = duration

                img_count = scene.get("image_count", 4)
                img_duration = duration / img_count

                updated_scenes.append(scene)
                print(
                    f"   ✅ Seg {i+1}/{len(scenes)}: {duration:.1f}s "
                    f"→ {img_count} visuals (~{img_duration:.1f}s each)"
                )

            except Exception as e:
                print(f"   ❌ Scene {i+1} failed permanently: {e}")
                failed_scenes.append(i + 1)
                # Still append the scene without audio so pipeline can decide
                # whether to continue or abort (handled by main.py status check)

        if failed_scenes:
            print(
                f"⚠️ {len(failed_scenes)} scene(s) failed audio generation: {failed_scenes}"
            )

        if not updated_scenes:
            print("❌ No scenes successfully generated. Aborting audio step.")
            return

        self.db.collection.update_one(
            {"_id": task["_id"]},
            {"$set": {"script_data": updated_scenes, "status": "voiced"}},
        )
        print(
            f"✅ Audio Generation Complete. ({len(updated_scenes)}/{len(scenes)} scenes)"
        )
