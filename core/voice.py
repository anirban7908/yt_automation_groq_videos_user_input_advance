import edge_tts
import asyncio
import os
from mutagen.mp3 import MP3
from core.db_manager import DBManager

MAX_RETRIES = 3
RETRY_DELAY = 2
MIN_AUDIO_DURATION = 0.5
FALLBACK_VOICE = "en-US-GuyNeural"


class VoiceEngine:
    def __init__(self):
        self.db = DBManager()

    async def _generate_single_segment(self, text, voice, path, rate="+10%"):
        """Generates a single TTS audio segment with retry and fallback logic."""
        last_error = None

        # ── Primary & Fallback Voice Attempts ──
        voices_to_try = [voice]
        if voice != FALLBACK_VOICE:
            voices_to_try.append(FALLBACK_VOICE)

        for current_voice in voices_to_try:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    communicate = edge_tts.Communicate(text, current_voice, rate=rate)
                    await communicate.save(path)

                    if not os.path.exists(path) or os.path.getsize(path) < 1000:
                        raise ValueError(f"File too small/missing: {path}")

                    duration = MP3(path).info.length
                    if duration < MIN_AUDIO_DURATION:
                        raise ValueError(f"Duration too short: {duration:.2f}s")

                    return path, duration

                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY)

            print(
                f"      ⚠️ Failed with voice {current_voice}. Moving to next option..."
            )

        raise RuntimeError(f"All attempts failed for scene. Last error: {last_error}")

    async def _process_scene_task(self, i, scene, folder, selected_voice):
        """Worker function for a single scene to be used in parallel gathering."""
        filename = f"voice_{i}.mp3"
        path = os.path.join(folder, filename)
        text = scene["text"]

        try:
            audio_path, duration = await self._generate_single_segment(
                text, selected_voice, path
            )
            scene["audio_path"] = audio_path
            scene["duration"] = duration
            return scene, None  # Success
        except Exception as e:
            return None, (i + 1, str(e))  # Failure info

    async def generate_audio(self):
        task = self.db.collection.find_one({"status": "scripted"})
        if not task:
            return

        folder = task.get("folder_path")
        os.makedirs(folder, exist_ok=True)  # Ensure directory exists

        scenes = task.get("script_data", [])
        selected_voice = task.get("voice_model", FALLBACK_VOICE)

        print(
            f"🎙️ Generating Audio ({len(scenes)} segments) in parallel using '{selected_voice}'..."
        )

        # ── Create and Run Tasks Simultaneously ──
        tasks = [
            self._process_scene_task(i, scene, folder, selected_voice)
            for i, scene in enumerate(scenes)
        ]

        results = await asyncio.gather(*tasks)

        updated_scenes = []
        failed_scenes = []

        for scene_result, error_result in results:
            if scene_result:
                updated_scenes.append(scene_result)
            else:
                failed_scenes.append(error_result)

        if failed_scenes:
            print(f"❌ Audio generation failed for scenes: {failed_scenes}")
            print("🚨 Aborting: Status will NOT be updated to 'voiced'.")
            return

        # ── Success: Atomic Update ──
        self.db.collection.update_one(
            {"_id": task["_id"]},
            {"$set": {"script_data": updated_scenes, "status": "voiced"}},
        )
        print(f"✅ Audio Generation Complete. ({len(updated_scenes)} scenes)")
