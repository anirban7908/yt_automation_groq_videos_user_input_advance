import os
import gc
import random
import textwrap
import time
import shutil
import numpy as np
import whisper
import torch
import moviepy.video.fx as vfx
import moviepy.audio.fx as afx
from moviepy import (
    AudioFileClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
    CompositeAudioClip,
)
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import glob
from core.db_manager import DBManager
from rapidfuzz import fuzz

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

NICHE_STYLES = {
    "space": {
        "color_grade": {
            "r": 0.85,
            "g": 0.90,
            "b": 1.20,
            "contrast": 1.3,
            "saturation": 1.4,
        },
        "film_grain": 0.3,
    },
    "tech_ai": {
        "color_grade": {
            "r": 0.80,
            "g": 0.95,
            "b": 1.25,
            "contrast": 1.25,
            "saturation": 0.9,
        },
        "film_grain": 0.15,
    },
    "psychology": {
        "color_grade": {
            "r": 1.0,
            "g": 0.92,
            "b": 0.85,
            "contrast": 1.15,
            "saturation": 0.85,
        },
        "film_grain": 0.2,
    },
    "health_wellness": {
        "color_grade": {
            "r": 0.95,
            "g": 1.10,
            "b": 0.95,
            "contrast": 1.1,
            "saturation": 1.2,
        },
        "film_grain": 0.05,
    },
    "animals_nature": {
        "color_grade": {
            "r": 0.95,
            "g": 1.15,
            "b": 0.85,
            "contrast": 1.2,
            "saturation": 1.6,
        },
        "film_grain": 0.1,
    },
    "finance_economy": {
        "color_grade": {
            "r": 0.95,
            "g": 0.95,
            "b": 0.95,
            "contrast": 1.2,
            "saturation": 0.8,
        },
        "film_grain": 0.1,
    },
    "bizarre_facts": {
        "color_grade": {
            "r": 1.10,
            "g": 0.85,
            "b": 1.10,
            "contrast": 1.35,
            "saturation": 1.4,
        },
        "film_grain": 0.35,
    },
    "history_world": {
        "color_grade": {
            "r": 1.15,
            "g": 1.00,
            "b": 0.75,
            "contrast": 1.15,
            "saturation": 0.9,
        },
        "film_grain": 0.45,
    },
    "default": {
        "color_grade": {
            "r": 1.0,
            "g": 1.0,
            "b": 1.0,
            "contrast": 1.1,
            "saturation": 1.1,
        },
        "film_grain": 0.1,
    },
}


class VideoAssembler:
    def __init__(self):
        self.db = DBManager()
        torch.set_num_threads(2)
        self.model = None
        self.clip_model = None
        self.clip_preprocess = None
        self.clip_device = "cpu"

    def _load_clip(self):
        if self.clip_model is None:
            try:
                import clip

                print("🎯 Loading CLIP model for AI-powered scene detection...")
                self.clip_model, self.clip_preprocess = clip.load(
                    "ViT-B/32", device=self.clip_device, jit=False
                )
                self.clip_model.eval()
                print("✅ CLIP model ready.")
            except Exception as e:
                print(f"⚠️ CLIP load failed: {e} — falling back to OpenCV.")
                self.clip_model = "unavailable"

    def _unload_clip(self):
        if self.clip_model not in (None, "unavailable"):
            del self.clip_model
            del self.clip_preprocess
            self.clip_model = None
            self.clip_preprocess = None
            gc.collect()
            print("🧹 CLIP model unloaded from memory.")

    def _load_whisper(self):
        if self.model is None:
            print("🧠 Loading Whisper model...")
            self.model = whisper.load_model("base")

    def _unload_whisper(self):
        if self.model is not None:
            del self.model
            self.model = None
            gc.collect()
            print("🧹 Whisper model unloaded from memory.")

    def _find_best_start(self, video_path, required_duration, keyword=""):
        try:
            import cv2

            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 24
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            total_duration = total_frames / fps
            max_start = total_duration - required_duration
            if max_start <= 0.5:
                cap.release()
                return 0.0
        except Exception:
            return 0.0

        if self.clip_model not in (None, "unavailable") and keyword:
            try:
                import clip as clip_lib

                text_tokens = clip_lib.tokenize([keyword]).to(self.clip_device)
                with torch.no_grad():
                    text_features = self.clip_model.encode_text(text_tokens)
                    text_features = text_features / text_features.norm(
                        dim=-1, keepdim=True
                    )

                sample_step = max(6, int(fps))
                frame_scores = []
                max_frame = int(
                    min(total_frames, (max_start + required_duration) * fps)
                )

                for fi in range(0, max_frame, sample_step):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                    ret, frame = cap.read()
                    if not ret or (fi / fps) > max_start:
                        break

                    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    img_tensor = (
                        self.clip_preprocess(pil_img).unsqueeze(0).to(self.clip_device)
                    )

                    with torch.no_grad():
                        img_features = self.clip_model.encode_image(img_tensor)
                        img_features = img_features / img_features.norm(
                            dim=-1, keepdim=True
                        )
                        score = float((img_features @ text_features.T).squeeze())

                    frame_scores.append((fi / fps, score))
                cap.release()

                if frame_scores:
                    return max(frame_scores, key=lambda x: x[1])[0]
            except Exception:
                try:
                    cap.release()
                except:
                    pass

        safe_start = random.uniform(0, max_start * 0.6) if max_start > 1 else 0.0
        return safe_start

    def _apply_color_grade(self, pil_image, grade):
        r, g, b = pil_image.split()
        r = ImageEnhance.Brightness(r).enhance(grade.get("r", 1.0))
        g = ImageEnhance.Brightness(g).enhance(grade.get("g", 1.0))
        b = ImageEnhance.Brightness(b).enhance(grade.get("b", 1.0))
        img = Image.merge("RGB", (r, g, b))
        img = ImageEnhance.Contrast(img).enhance(grade.get("contrast", 1.0))
        return ImageEnhance.Color(img).enhance(grade.get("saturation", 1.0))

    def _apply_film_grain(self, frame_np, intensity=0.15):
        if intensity <= 0:
            return frame_np
        noise = np.random.randint(
            -int(30 * intensity), int(30 * intensity), frame_np.shape, dtype=np.int16
        )
        return np.clip(frame_np.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    def _apply_image_effects(self, clip, img_duration):
        """Lazy frame generation to fix OOM RAM crash. Evaluated per frame."""
        effect = random.choice(
            [
                "ken_burns_zoom_in",
                "pan_left_right",
                "pan_right_left",
                "pan_top_bottom",
                "fade_in_out",
                "color_grade_warm",
            ]
        )
        print(f"         ✨ Image effect: {effect}")

        W, H = 1080, 1920
        pil_base = Image.fromarray(clip.get_frame(0)).resize((W, H), Image.LANCZOS)

        # Pre-compute static wide/tall images to save massive CPU time during rendering
        wide_img = (
            pil_base.resize((int(W * 1.2), H), Image.LANCZOS)
            if "pan" in effect
            else None
        )
        tall_img = (
            pil_base.resize((W, int(H * 1.2)), Image.LANCZOS)
            if effect == "pan_top_bottom"
            else None
        )

        def make_frame(t):
            progress = min(1.0, t / img_duration)

            if effect == "ken_burns_zoom_in":
                scale = 1.0 + 0.15 * progress
                new_w, new_h = int(W * scale), int(H * scale)
                # Use BILINEAR for dynamic CPU speed
                img = pil_base.resize((new_w, new_h), Image.BILINEAR)
                left, top = (new_w - W) // 2, (new_h - H) // 2
                return np.array(img.crop((left, top, left + W, top + H)))

            elif effect == "pan_left_right" and wide_img:
                offset = int((wide_img.width - W) * progress)
                return np.array(wide_img.crop((offset, 0, offset + W, H)))

            elif effect == "pan_right_left" and wide_img:
                offset = int((wide_img.width - W) * (1 - progress))
                return np.array(wide_img.crop((offset, 0, offset + W, H)))

            elif effect == "pan_top_bottom" and tall_img:
                offset = int((tall_img.height - H) * progress)
                return np.array(tall_img.crop((0, offset, W, offset + H)))

            elif effect == "fade_in_out":
                fade = min(1.0, img_duration * 0.3)
                alpha_in = min(1.0, t / fade)
                alpha_out = 1.0 - max(
                    0.0, min(1.0, (t - (img_duration - fade)) / (fade + 0.001))
                )
                return np.array(
                    Image.blend(
                        Image.new("RGB", (W, H), (0, 0, 0)),
                        pil_base,
                        min(alpha_in, alpha_out),
                    )
                )

            elif effect == "color_grade_warm":
                r, g, b = pil_base.split()
                r = ImageEnhance.Brightness(r).enhance(1.15)
                b = ImageEnhance.Brightness(b).enhance(0.85)
                img = ImageEnhance.Color(
                    ImageEnhance.Contrast(Image.merge("RGB", (r, g, b))).enhance(1.1)
                ).enhance(1.2)
                return np.array(img)

            return np.array(pil_base)

        return clip.with_duration(img_duration).transform(
            lambda gf, t: make_frame(t), apply_to="video"
        )

    def _make_clip(self, path, img_duration, niche_style, keyword=""):
        try:
            if not os.path.exists(path):
                print(
                    f"      🚨 DEBUG SILENT FAIL: File not found on hard drive -> {path}"
                )
                return None

            VIDEO_EXTS, IMAGE_EXTS = (".mp4",), (".jpg", ".jpeg", ".png", ".webp")
            if any(
                path.lower().endswith(ext)
                for ext in (".mov", ".webm", ".ogv", ".avi", ".mkv")
            ):
                print(
                    f"      🚨 DEBUG SILENT FAIL: Unsupported file extension -> {path}"
                )
                return None

            grade = niche_style.get("color_grade", {})
            is_video = any(path.lower().endswith(ext) for ext in VIDEO_EXTS)
            is_image = any(path.lower().endswith(ext) for ext in IMAGE_EXTS)

            if not is_video and not is_image:
                print(
                    f"      🚨 DEBUG SILENT FAIL: File is neither a recognized video nor image -> {path}"
                )
                return None

            if is_video:
                best_start = self._find_best_start(path, img_duration, keyword=keyword)
                clip = VideoFileClip(path, audio=False)

                if clip.duration < img_duration:
                    clip = (
                        vfx.Loop(duration=img_duration)
                        .apply(clip)
                        .subclipped(0, img_duration)
                    )
                else:
                    clip = clip.subclipped(
                        best_start, min(best_start + img_duration, clip.duration)
                    )

                if grade:
                    clip = clip.transform(
                        lambda gf, t: np.array(
                            self._apply_color_grade(Image.fromarray(gf(t)), grade)
                        ),
                        apply_to="video",
                    )

                clip = clip.resized(height=1920)
                if clip.w < 1080:
                    clip = clip.resized(width=1080)
                return clip.cropped(
                    x_center=clip.w / 2, y_center=clip.h / 2, width=1080, height=1920
                )

            else:
                clip = ImageClip(path).with_duration(img_duration)
                clip = clip.resized(height=1920)
                if clip.w < 1080:
                    clip = clip.resized(width=1080)
                clip = clip.cropped(
                    x_center=clip.w / 2, y_center=clip.h / 2, width=1080, height=1920
                )

                if grade:
                    graded = np.array(
                        self._apply_color_grade(
                            Image.fromarray(clip.get_frame(0)), grade
                        )
                    )
                    clip = clip.transform(lambda gf, t: graded, apply_to="video")

                return self._apply_image_effects(clip, img_duration)

        except Exception as e:
            print(f"⚠️ Error processing visual {path}: {e}")
            return None

    def _write_base_video(self, scenes, folder, niche):
        niche_style = NICHE_STYLES.get(niche, NICHE_STYLES["default"])
        grain_intensity = niche_style.get("film_grain", 0.1)
        scene_files = []

        for i, scene in enumerate(scenes):
            audio_path = os.path.normpath(
                os.path.join(PROJECT_ROOT, scene["audio_path"])
            )
            if not os.path.exists(audio_path):
                continue

            audio_clip = AudioFileClip(audio_path)
            duration = audio_clip.duration
            visual_paths = scene.get("image_paths", [])

            if not visual_paths:
                audio_clip.close()
                continue

            clip_durations = []
            words = scene.get("whisper_words", [])
            trigger_words = scene.get("trigger_words", [])
            num_clips = len(visual_paths)

            if not trigger_words or len(trigger_words) != num_clips:
                clip_durations = [duration / max(1, num_clips)] * num_clips
                words = []

            if words and num_clips > 1:
                cut_times = [0.0]
                word_idx = 0

                for c_idx in range(1, num_clips):
                    target = "".join(
                        e
                        for e in (
                            trigger_words[c_idx].lower().strip()
                            if c_idx < len(trigger_words)
                            else ""
                        )
                        if e.isalnum()
                    )
                    found_time = None

                    if target:
                        for idx in range(word_idx, len(words)):
                            clean_w = "".join(
                                e for e in words[idx]["word"] if e.isalnum()
                            ).lower()

                            # 🟢 THE SYNC FIX: Semantic Fuzzy Matching
                            score = fuzz.ratio(target, clean_w)

                            if score >= 80 or (
                                len(target) > 3 and target in clean_w and score > 50
                            ):
                                found_time = words[idx]["start"]
                                word_idx = idx + 1
                                break

                    if found_time is not None and found_time > cut_times[-1]:
                        cut_times.append(found_time)
                    else:
                        remaining_clips = num_clips - c_idx
                        remaining_time = duration - cut_times[-1]
                        cut_times.append(
                            cut_times[-1] + (remaining_time / (remaining_clips + 1))
                        )

                for c_idx in range(num_clips):
                    if c_idx == num_clips - 1:
                        clip_dur = duration - cut_times[c_idx]
                    else:
                        clip_dur = cut_times[c_idx + 1] - cut_times[c_idx]
                    clip_durations.append(max(0.5, clip_dur))
            else:
                clip_durations = [duration / max(1, num_clips)] * num_clips

            scene_clips = []
            scene_keywords = scene.get("keywords", [])

            for vi, raw_path in enumerate(visual_paths):
                path = os.path.normpath(os.path.join(PROJECT_ROOT, raw_path))
                keyword = scene_keywords[vi] if vi < len(scene_keywords) else ""
                scene_context = scene.get("text", "")
                visual_intent = f"{keyword}. {scene_context}"[:240]
                clip = self._make_clip(
                    path, clip_durations[vi], niche_style, keyword=visual_intent
                )
                if clip is not None:
                    scene_clips.append(clip)

            if not scene_clips:
                print(
                    f"      🚨 DEBUG SILENT FAIL: No valid visual clips were generated for scene {i}! Skipping scene."
                )
                audio_clip.close()
                continue

            if grain_intensity > 0:
                scene_clips = [
                    c.transform(
                        lambda gf, t, g=grain_intensity: self._apply_film_grain(
                            gf(t), g
                        ),
                        apply_to="video",
                    )
                    for c in scene_clips
                ]

            scene_video = concatenate_videoclips(
                scene_clips, method="chain"
            ).with_audio(audio_clip)
            temp_path = os.path.join(folder, f"_temp_scene_{i}.mp4")
            print(f"   💾 Writing scene {i+1}/{len(scenes)} to disk...")

            try:
                scene_video.write_videofile(
                    temp_path,
                    fps=24,
                    codec="libx264",
                    audio_codec="aac",
                    bitrate="4000k",
                    threads=2,
                    preset="ultrafast",
                    logger="bar",
                )
                scene_files.append(temp_path)
            except Exception as e:
                print(f"\n      🚨 MOVIEPY CRASH REVEALED: {e}\n")
            finally:
                try:
                    scene_video.close()
                except:
                    pass
                try:
                    audio_clip.close()
                except:
                    pass
                for c in scene_clips:
                    try:
                        c.close()
                    except:
                        pass
                gc.collect()

        if not scene_files:
            return None, None

        print("🔗 Concatenating temporary scene files into base video...")
        temp_clips = [VideoFileClip(p) for p in scene_files]
        base_video = concatenate_videoclips(temp_clips, method="chain")

        base_path = os.path.join(folder, "_BASE_VIDEO_TEMP.mp4")
        full_audio_path = os.path.join(folder, "FULL_AUDIO_TEMP.mp3")

        base_video.audio.write_audiofile(full_audio_path, logger=None)

        final_audio = base_video.audio
        music_dir = os.path.join(PROJECT_ROOT, "assets", "music")

        if os.path.exists(music_dir):
            music_files = glob.glob(os.path.join(music_dir, "*.mp3")) + glob.glob(
                os.path.join(music_dir, "*.wav")
            )
            if music_files:
                bg_clip = AudioFileClip(random.choice(music_files))
                if bg_clip.duration < base_video.duration:
                    bg_clip = afx.AudioLoop(duration=base_video.duration).apply(bg_clip)
                else:
                    bg_clip = bg_clip.subclipped(0, base_video.duration)
                bg_clip = afx.MultiplyVolume(0.10).apply(bg_clip)
                final_audio = CompositeAudioClip([base_video.audio, bg_clip])

        base_video = base_video.with_audio(final_audio)
        base_video.write_videofile(
            base_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            bitrate="4000k",
            threads=2,
            preset="ultrafast",
            logger="bar",
        )

        for c in temp_clips:
            try:
                c.close()
            except:
                pass
        try:
            base_video.close()
        except:
            pass
        gc.collect()

        for p in scene_files:
            try:
                os.remove(p)
            except:
                pass

        return base_path, full_audio_path

    def _draw_text_on_video(self, base_path, full_audio_path, out_path, video_title):
        self._load_whisper()
        print("📝 Processing audio for English captions...")
        result = self.model.transcribe(
            full_audio_path, word_timestamps=True, fp16=False
        )
        self._unload_whisper()

        words = [
            word for segment in result["segments"] for word in segment.get("words", [])
        ]
        if not words:
            shutil.copy(base_path, out_path)
            return

        for i in range(len(words) - 1):
            words[i]["end"] = min(words[i]["end"], words[i + 1]["start"])

        import sys

        FONT_CANDIDATES = (
            [r"C:\Windows\Fonts\ariblk.ttf"]
            if sys.platform == "win32"
            else [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )
        caption_font, title_font = ImageFont.load_default(), ImageFont.load_default()

        for font_path in FONT_CANDIDATES:
            if os.path.exists(font_path):
                caption_font = ImageFont.truetype(font_path, 90)
                title_font = ImageFont.truetype(font_path, 80)
                break

        wrapped_title = "\n".join(textwrap.wrap(video_title, width=20))

        def draw_frame(get_frame, t):
            frame = get_frame(t)
            active_word = next(
                (
                    w["word"].strip().upper()
                    for w in words
                    if w["start"] <= t <= w["end"]
                ),
                None,
            )
            show_title = t <= 3.0

            if not active_word and not show_title:
                return frame

            img = Image.fromarray(frame)
            draw = ImageDraw.Draw(img)

            if show_title:
                bbox = draw.multiline_textbbox((0, 0), wrapped_title, font=title_font)
                draw.multiline_text(
                    ((1080 - (bbox[2] - bbox[0])) / 2, 300),
                    wrapped_title,
                    font=title_font,
                    fill="yellow",
                    stroke_width=5,
                    stroke_fill="black",
                    align="center",
                )

            if active_word:
                bbox = draw.textbbox((0, 0), active_word, font=caption_font)
                draw.text(
                    ((1080 - (bbox[2] - bbox[0])) / 2, 1500),
                    active_word,
                    font=caption_font,
                    fill="white",
                    stroke_width=4,
                    stroke_fill="black",
                )

            return np.array(img)

        from moviepy import VideoFileClip as VFC

        base_video = VFC(base_path)
        final_video = base_video.transform(draw_frame).with_audio(base_video.audio)
        final_video.write_videofile(
            out_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            bitrate="4000k",
            threads=2,
            preset="ultrafast",
            logger="bar",
        )

        try:
            final_video.close()
            base_video.close()
        except:
            pass
        gc.collect()

    def _cleanup_intermediate_files(
        self, folder_path, final_video_name="FINAL_VIDEO.mp4"
    ):
        print("      🧹 Cleaning up intermediate clips and audio files...")
        time.sleep(2)
        deleted_files = 0
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if (
                os.path.isfile(file_path)
                and filename != final_video_name
                and not filename.startswith("FINAL_VIDEO_METADATA")
            ):
                try:
                    os.remove(file_path)
                    deleted_files += 1
                except:
                    pass
        print(f"      ✅ Cleanup complete. Deleted {deleted_files} raw files.")

    def assemble(self):
        task = self.db.collection.find_one({"status": "ready_to_assemble"})
        if not task:
            return

        scenes = task.get("script_data", [])
        folder = os.path.normpath(os.path.join(PROJECT_ROOT, task["folder_path"]))
        video_title = task.get("title", "Breaking News").upper()
        niche = task.get("niche", "default").lower()
        os.makedirs(folder, exist_ok=True)

        print(f"🎞️ Assembling {len(scenes)} scenes | niche: '{niche}'")

        self._load_whisper()
        for scene in scenes:
            audio_path = os.path.normpath(
                os.path.join(PROJECT_ROOT, scene.get("audio_path", ""))
            )
            if os.path.exists(audio_path):
                res = self.model.transcribe(
                    audio_path, word_timestamps=True, fp16=False
                )
                scene["whisper_words"] = [
                    w for s in res.get("segments", []) for w in s.get("words", [])
                ]
        self._unload_whisper()

        self._load_clip()
        base_path, full_audio_path = self._write_base_video(scenes, folder, niche)
        self._unload_clip()

        if not base_path:
            return

        out_path = os.path.join(folder, "FINAL_VIDEO.mp4")
        self._draw_text_on_video(base_path, full_audio_path, out_path, video_title)

        for temp in [base_path, full_audio_path]:
            if os.path.exists(temp):
                try:
                    os.remove(temp)
                except:
                    pass

        self.db.collection.update_one(
            {"_id": task["_id"]},
            {"$set": {"status": "ready_to_upload", "final_video_path": out_path}},
        )
        print(f"🎉 Synchronized Video Ready: {out_path}")
        self._cleanup_intermediate_files(folder)
