import os
import gc
import random
import textwrap
import whisper
import moviepy.video.fx as vfx
from moviepy import (
    AudioFileClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
    CompositeAudioClip,
)
import moviepy.audio.fx as afx
import glob
from core.db_manager import DBManager
import time
import torch
import shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

NICHE_STYLES = {
    "space": {
        "allowed_transitions": ["crossfade", "zoom_cut", "light_leak"],
        "color_grade": {
            "r": 0.85,
            "g": 0.90,
            "b": 1.20,
            "contrast": 1.3,
            "saturation": 1.4,
        },
        "film_grain": 0.3,
        "light_leak_color": (80, 60, 180),
    },
    "tech_ai": {
        "allowed_transitions": ["crossfade", "whip_pan", "zoom_cut"],
        "color_grade": {
            "r": 0.80,
            "g": 0.95,
            "b": 1.25,
            "contrast": 1.25,
            "saturation": 0.9,
        },
        "film_grain": 0.15,
        "light_leak_color": (40, 120, 255),
    },
    "psychology": {
        "allowed_transitions": ["crossfade", "zoom_cut"],
        "color_grade": {
            "r": 1.0,
            "g": 0.92,
            "b": 0.85,
            "contrast": 1.15,
            "saturation": 0.85,
        },
        "film_grain": 0.2,
        "light_leak_color": (180, 100, 60),
    },
    "health_wellness": {
        "allowed_transitions": ["crossfade", "zoom_cut"],
        "color_grade": {
            "r": 0.95,
            "g": 1.10,
            "b": 0.95,
            "contrast": 1.1,
            "saturation": 1.2,
        },
        "film_grain": 0.05,
        "light_leak_color": (150, 255, 150),
    },
    "animals_nature": {
        "allowed_transitions": ["crossfade", "whip_pan", "zoom_cut", "light_leak"],
        "color_grade": {
            "r": 0.95,
            "g": 1.15,
            "b": 0.85,
            "contrast": 1.2,
            "saturation": 1.6,
        },
        "film_grain": 0.1,
        "light_leak_color": (255, 230, 100),
    },
    "finance_economy": {
        "allowed_transitions": ["crossfade", "zoom_cut"],
        "color_grade": {
            "r": 0.95,
            "g": 0.95,
            "b": 0.95,
            "contrast": 1.2,
            "saturation": 0.8,
        },
        "film_grain": 0.1,
        "light_leak_color": (220, 180, 60),
    },
    "bizarre_facts": {
        "allowed_transitions": ["crossfade", "whip_pan", "zoom_cut", "light_leak"],
        "color_grade": {
            "r": 1.10,
            "g": 0.85,
            "b": 1.10,
            "contrast": 1.35,
            "saturation": 1.4,
        },
        "film_grain": 0.35,
        "light_leak_color": (180, 50, 200),
    },
    "history_world": {
        "allowed_transitions": ["crossfade", "zoom_cut", "light_leak"],
        "color_grade": {
            "r": 1.15,
            "g": 1.00,
            "b": 0.75,
            "contrast": 1.15,
            "saturation": 0.9,
        },
        "film_grain": 0.45,
        "light_leak_color": (180, 130, 50),
    },
    "default": {
        "allowed_transitions": ["crossfade", "zoom_cut"],
        "color_grade": {
            "r": 1.0,
            "g": 1.0,
            "b": 1.0,
            "contrast": 1.1,
            "saturation": 1.1,
        },
        "film_grain": 0.1,
        "light_leak_color": (255, 255, 255),
    },
}


class VideoAssembler:
    def __init__(self):
        self.db = DBManager()
        torch.set_num_threads(2)
        self.model = None  # Whisper (lazy loaded)
        self.clip_model = None  # CLIP (lazy loaded, unloaded before Whisper)
        self.clip_preprocess = None
        self.clip_device = "cpu"

    # ─────────────────────────────────────────────
    # MODEL LIFECYCLE
    # ─────────────────────────────────────────────

    def _load_clip(self):
        """
        Load CLIP ViT-B/32 once and cache in self.clip_model.
        ~400MB RAM on CPU. Loaded once per assemble() call, then freed
        before Whisper loads so both never compete for RAM simultaneously.

        Install: pip install git+https://github.com/openai/CLIP.git
        """
        if self.clip_model is None:
            try:
                import clip

                print("🎯 Loading CLIP model for AI-powered scene detection...")
                self.clip_model, self.clip_preprocess = clip.load(
                    "ViT-B/32", device=self.clip_device, jit=False
                )
                self.clip_model.eval()
                print("✅ CLIP model ready.")
            except ImportError:
                print("⚠️ CLIP not installed — falling back to OpenCV motion scoring.")
                print("   Install: pip install git+https://github.com/openai/CLIP.git")
                self.clip_model = "unavailable"
            except Exception as e:
                print(f"⚠️ CLIP load failed: {e} — falling back to OpenCV.")
                self.clip_model = "unavailable"

    def _unload_clip(self):
        """Free CLIP RAM before Whisper loads."""
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
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print("🧹 Whisper model unloaded from memory.")

    # ─────────────────────────────────────────────
    # AI-POWERED SMART START (CLIP + OpenCV fallback)
    # ─────────────────────────────────────────────

    def _find_best_start(self, video_path, required_duration, keyword=""):
        """
        Finds the best starting timestamp for a video clip.

        STEP 1 — CLIP semantic scoring (DEFAULT, always attempted first):
            Encodes 'keyword' as text, scores every sampled frame against it,
            and picks the SINGLE BEST matching frame as the start point.
            This ensures the most visually relevant moment of the clip is used.
            e.g. keyword="WW2 submarine interior dim corridor wide shot" →
            CLIP finds the frame that actually looks like that scene.

        STEP 2 — OpenCV motion scoring (fallback if CLIP unavailable):
            Scores frames by pixel-diff motion and picks from top active moments.

        STEP 3 — Random start (last resort only).

        keyword: the cinematic search term generated by brain.py.
        """
        # ── Get video metadata ──
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
        except Exception as e:
            print(f"         ⚠️ Could not read video metadata: {e}")
            return 0.0

        # ── STEP 1: CLIP semantic scoring (DEFAULT) ──
        if self.clip_model == "unavailable":
            print(f"         ⚠️ CLIP unavailable — using OpenCV fallback.")
            print(
                f"            Install: pip install git+https://github.com/openai/CLIP.git"
            )
        elif self.clip_model is not None and keyword:
            try:
                import clip as clip_lib

                text_tokens = clip_lib.tokenize([keyword]).to(self.clip_device)
                with torch.no_grad():
                    text_features = self.clip_model.encode_text(text_tokens)
                    text_features = text_features / text_features.norm(
                        dim=-1, keepdim=True
                    )

                # Sample ~1 frame per second (CPU-friendly)
                sample_step = max(6, int(fps))
                frame_scores = []
                max_frame = int(
                    min(total_frames, (max_start + required_duration) * fps)
                )

                for fi in range(0, max_frame, sample_step):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                    ret, frame = cap.read()
                    if not ret:
                        break
                    timestamp = fi / fps
                    if timestamp > max_start:
                        break

                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(rgb)
                    img_tensor = (
                        self.clip_preprocess(pil_img).unsqueeze(0).to(self.clip_device)
                    )

                    with torch.no_grad():
                        img_features = self.clip_model.encode_image(img_tensor)
                        img_features = img_features / img_features.norm(
                            dim=-1, keepdim=True
                        )
                        score = float((img_features @ text_features.T).squeeze())

                    frame_scores.append((timestamp, score))

                cap.release()

                if frame_scores:
                    # Always pick the single best frame — no random variety
                    # This ensures the most relevant moment is always used
                    best_start = max(frame_scores, key=lambda x: x[1])[0]
                    print(
                        f"         🎯 CLIP start: {best_start:.1f}s — matched '{keyword}'"
                    )
                    return best_start

            except Exception as e:
                print(f"         ⚠️ CLIP scoring failed: {e} — falling back to OpenCV.")
                try:
                    cap.release()
                except:
                    pass

        # ── STEP 2: OpenCV pixel-diff fallback ──
        try:
            cap = cv2.VideoCapture(video_path)
            prev_frame = None
            frame_scores = []

            for fi in range(0, total_frames, 3):
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ret, frame = cap.read()
                if not ret:
                    break
                small = cv2.resize(frame, (64, 64))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if prev_frame is not None:
                    diff = cv2.absdiff(gray, prev_frame)
                    score = float(np.mean(diff))
                    timestamp = fi / fps
                    if timestamp <= max_start:
                        frame_scores.append((timestamp, score))
                prev_frame = gray

            cap.release()

            if frame_scores:
                top = sorted(frame_scores, key=lambda x: x[1], reverse=True)[:5]
                best_start = random.choice(top)[0]
                print(f"         🎯 OpenCV start: {best_start:.1f}s (motion-based)")
                return best_start

        except Exception as e:
            print(f"         ⚠️ OpenCV fallback also failed: {e}")

        # ── STEP 3: Random start last resort ──
        safe_start = random.uniform(0, max_start * 0.6) if max_start > 1 else 0.0
        print(f"         🎯 Random start: {safe_start:.1f}s")
        return safe_start

    # ─────────────────────────────────────────────
    # COLOR + GRAIN EFFECTS
    # ─────────────────────────────────────────────

    def _apply_color_grade(self, pil_image, grade):
        r, g, b = pil_image.split()
        r = ImageEnhance.Brightness(r).enhance(grade.get("r", 1.0))
        g = ImageEnhance.Brightness(g).enhance(grade.get("g", 1.0))
        b = ImageEnhance.Brightness(b).enhance(grade.get("b", 1.0))
        img = Image.merge("RGB", (r, g, b))
        img = ImageEnhance.Contrast(img).enhance(grade.get("contrast", 1.0))
        img = ImageEnhance.Color(img).enhance(grade.get("saturation", 1.0))
        return img

    def _apply_film_grain(self, frame_np, intensity=0.15):
        if intensity <= 0:
            return frame_np
        noise = np.random.randint(
            -int(30 * intensity), int(30 * intensity), frame_np.shape, dtype=np.int16
        )
        return np.clip(frame_np.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # ─────────────────────────────────────────────
    # IMAGE EFFECTS ENGINE (14 effects for static images)
    # ─────────────────────────────────────────────

    def _apply_image_effects(self, clip, img_duration):
        effect = random.choice(
            [
                "ken_burns_zoom_in",
                "ken_burns_zoom_out",
                "pan_left_right",
                "pan_right_left",
                "pan_top_bottom",
                "fade_in",
                "fade_out",
                "fade_in_out",
                "gaussian_blur_reveal",
                "color_grade_warm",
                "color_grade_cool",
                "vignette",
                "slow_zoom_with_fade",
                "slide_wipe_left",
            ]
        )
        print(f"         ✨ Image effect: {effect}")
        W, H = 1080, 1920
        fps = 24
        total_frames = int(img_duration * fps)
        base_frame = clip.get_frame(0)
        pil_base = Image.fromarray(base_frame).resize((W, H), Image.LANCZOS)

        def make_frames(effect_name):
            frames = []
            for fi in range(total_frames):
                t = fi / fps
                progress = fi / max(total_frames - 1, 1)
                img = pil_base.copy()

                if effect_name == "ken_burns_zoom_in":
                    scale = 1.0 + 0.15 * progress
                    new_w, new_h = int(W * scale), int(H * scale)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    img = img.crop(
                        (
                            (new_w - W) // 2,
                            (new_h - H) // 2,
                            (new_w - W) // 2 + W,
                            (new_h - H) // 2 + H,
                        )
                    )
                elif effect_name == "ken_burns_zoom_out":
                    scale = 1.15 - 0.15 * progress
                    new_w, new_h = int(W * scale), int(H * scale)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    img = img.crop(
                        (
                            (new_w - W) // 2,
                            (new_h - H) // 2,
                            (new_w - W) // 2 + W,
                            (new_h - H) // 2 + H,
                        )
                    )
                elif effect_name == "pan_left_right":
                    wide = img.resize((int(W * 1.2), H), Image.LANCZOS)
                    offset = int((wide.width - W) * progress)
                    img = wide.crop((offset, 0, offset + W, H))
                elif effect_name == "pan_right_left":
                    wide = img.resize((int(W * 1.2), H), Image.LANCZOS)
                    offset = int((wide.width - W) * (1 - progress))
                    img = wide.crop((offset, 0, offset + W, H))
                elif effect_name == "pan_top_bottom":
                    tall = img.resize((W, int(H * 1.2)), Image.LANCZOS)
                    offset = int((tall.height - H) * progress)
                    img = tall.crop((0, offset, W, offset + H))
                elif effect_name == "fade_in":
                    alpha = min(1.0, t / min(1.0, img_duration * 0.4))
                    img = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), img, alpha)
                elif effect_name == "fade_out":
                    fade_start = img_duration * 0.6
                    alpha = 1.0 - max(
                        0.0,
                        min(
                            1.0, (t - fade_start) / (img_duration - fade_start + 0.001)
                        ),
                    )
                    img = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), img, alpha)
                elif effect_name == "fade_in_out":
                    fade = min(1.0, img_duration * 0.3)
                    alpha_in = min(1.0, t / fade)
                    alpha_out = 1.0 - max(
                        0.0, min(1.0, (t - (img_duration - fade)) / (fade + 0.001))
                    )
                    img = Image.blend(
                        Image.new("RGB", (W, H), (0, 0, 0)),
                        img,
                        min(alpha_in, alpha_out),
                    )
                elif effect_name == "gaussian_blur_reveal":
                    blur_radius = max(0, 12 * (1 - progress))
                    if blur_radius > 0.5:
                        img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
                elif effect_name == "color_grade_warm":
                    r, g, b = img.split()
                    r = ImageEnhance.Brightness(r).enhance(1.15)
                    b = ImageEnhance.Brightness(b).enhance(0.85)
                    img = ImageEnhance.Color(
                        ImageEnhance.Contrast(Image.merge("RGB", (r, g, b))).enhance(
                            1.1
                        )
                    ).enhance(1.2)
                elif effect_name == "color_grade_cool":
                    r, g, b = img.split()
                    r = ImageEnhance.Brightness(r).enhance(0.88)
                    b = ImageEnhance.Brightness(b).enhance(1.12)
                    img = ImageEnhance.Color(
                        ImageEnhance.Contrast(Image.merge("RGB", (r, g, b))).enhance(
                            1.15
                        )
                    ).enhance(0.9)
                elif effect_name == "vignette":
                    vignette = Image.new("RGB", (W, H), (0, 0, 0))
                    mask = Image.new("L", (W, H), 0)
                    m = 0.35
                    ImageDraw.Draw(mask).ellipse(
                        [int(W * m), int(H * m), int(W * (1 - m)), int(H * (1 - m))],
                        fill=255,
                    )
                    mask = mask.filter(ImageFilter.GaussianBlur(radius=200))
                    img = Image.composite(img, vignette, mask)
                elif effect_name == "slow_zoom_with_fade":
                    scale = 1.0 + 0.12 * progress
                    new_w, new_h = int(W * scale), int(H * scale)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    img = img.crop(
                        (
                            (new_w - W) // 2,
                            (new_h - H) // 2,
                            (new_w - W) // 2 + W,
                            (new_h - H) // 2 + H,
                        )
                    )
                    fade = min(1.0, img_duration * 0.3)
                    img = Image.blend(
                        Image.new("RGB", (W, H), (0, 0, 0)), img, min(1.0, t / fade)
                    )
                elif effect_name == "slide_wipe_left":
                    wipe_w = int(W * min(1.0, progress * 2))
                    black = Image.new("RGB", (W, H), (0, 0, 0))
                    black.paste(img.crop((0, 0, wipe_w, H)), (0, 0))
                    img = black

                frames.append(np.array(img))
            return frames

        frames = make_frames(effect)

        def make_frame(t):
            return frames[min(int(t * fps), total_frames - 1)]

        return clip.with_duration(img_duration).transform(
            lambda gf, t: make_frame(t), apply_to="video"
        )

    # ─────────────────────────────────────────────
    # TRANSITION ENGINE
    # ─────────────────────────────────────────────

    def _make_transition_frames(self, clip_a, clip_b, transition, leak_color):
        W, H, fps = 1080, 1920, 24
        durations = {
            "crossfade": 0.25,
            "whip_pan": 0.15,
            "light_leak": 0.20,
            "zoom_cut": 0.10,
        }
        duration = durations.get(transition, 0.2)
        n_frames = int(duration * fps)
        frames = []
        try:
            for fi in range(n_frames):
                progress = fi / n_frames
                t_a = min(
                    clip_a.duration - 0.01,
                    max(0, clip_a.duration - duration + (fi / fps)),
                )
                t_b = min(clip_b.duration - 0.01, max(0, fi / fps))
                frame_a = clip_a.get_frame(t_a)
                frame_b = clip_b.get_frame(t_b)

                if transition == "crossfade":
                    out = np.clip(
                        frame_a.astype(np.float32) * (1 - progress)
                        + frame_b.astype(np.float32) * progress,
                        0,
                        255,
                    ).astype(np.uint8)
                elif transition == "whip_pan":
                    blur_r = int(40 * np.sin(progress * np.pi))
                    base = frame_a if progress < 0.5 else frame_b
                    pil = Image.fromarray(base)
                    if blur_r > 0:
                        pil = pil.filter(ImageFilter.GaussianBlur(radius=blur_r))
                    out = np.array(pil)
                elif transition == "light_leak":
                    base = frame_a if progress < 0.5 else frame_b
                    alpha = float(np.sin(progress * np.pi)) * 0.7
                    out = np.array(
                        Image.blend(
                            Image.fromarray(base),
                            Image.new("RGB", (W, H), leak_color),
                            alpha,
                        )
                    )
                elif transition == "zoom_cut":
                    scale = 1.15 - (0.15 * progress)
                    new_w, new_h = int(W * scale), int(H * scale)
                    pil = Image.fromarray(frame_b).resize((new_w, new_h), Image.LANCZOS)
                    left, top = (new_w - W) // 2, (new_h - H) // 2
                    out = np.array(pil.crop((left, top, left + W, top + H)))
                else:
                    out = frame_b

                frames.append(out)
        except Exception as e:
            print(f"      ⚠️ Transition '{transition}' error: {e}")
        return frames

    # ─────────────────────────────────────────────
    # CLIP BUILDER
    # ─────────────────────────────────────────────

    def _make_clip(self, path, img_duration, niche_style, keyword=""):
        try:
            if not os.path.exists(path):
                print(f"⚠️ Missing Visual File: {path}")
                return None

            VIDEO_EXTS = (".mp4",)
            IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
            REJECT_EXTS = (".mov", ".webm", ".ogv", ".ogg", ".avi", ".mkv", ".flv")

            path_lower = path.lower()
            if any(path_lower.endswith(ext) for ext in REJECT_EXTS):
                print(f"⚠️ Unsupported video format skipped: {os.path.basename(path)}")
                return None

            grade = niche_style.get("color_grade", {})
            is_video = any(path_lower.endswith(ext) for ext in VIDEO_EXTS)
            is_image = any(path_lower.endswith(ext) for ext in IMAGE_EXTS)

            if not is_video and not is_image:
                return None

            if is_video:
                # ── AI smart start: CLIP finds the frame matching the keyword ──
                best_start = self._find_best_start(path, img_duration, keyword=keyword)
                clip = VideoFileClip(path, audio=False)

                # ── NEW: Natural Cinematic Pacing (No Speed Ramp) ────────────────
                if clip.duration < img_duration:
                    # Clip is too short: loop it to fill the required duration naturally
                    clip = vfx.Loop(duration=img_duration).apply(clip)
                    clip = clip.subclipped(0, img_duration)
                else:
                    # Clip is long enough: cut it naturally at the exact duration
                    end = min(best_start + img_duration, clip.duration)
                    clip = clip.subclipped(best_start, end)

                if grade:

                    def grade_video_frame(get_frame, t):
                        return np.array(
                            self._apply_color_grade(
                                Image.fromarray(get_frame(t)), grade
                            )
                        )

                    clip = clip.transform(grade_video_frame, apply_to="video")

                clip = clip.resized(height=1920)
                if clip.w < 1080:
                    clip = clip.resized(width=1080)
                clip = clip.cropped(
                    x_center=clip.w / 2, y_center=clip.h / 2, width=1080, height=1920
                )
                return clip

            else:
                # ── Static image path ──
                clip = ImageClip(path).with_duration(img_duration)
                clip = clip.resized(height=1920)
                if clip.w < 1080:
                    clip = clip.resized(width=1080)
                clip = clip.cropped(
                    x_center=clip.w / 2, y_center=clip.h / 2, width=1080, height=1920
                )

                if grade:
                    base_frame = clip.get_frame(0)
                    graded = np.array(
                        self._apply_color_grade(Image.fromarray(base_frame), grade)
                    )
                    clip = clip.transform(lambda gf, t: graded, apply_to="video")

                clip = self._apply_image_effects(clip, img_duration)
                return clip

        except Exception as e:
            print(f"⚠️ Error processing visual {path}: {e}")
            return None

    # ─────────────────────────────────────────────
    # BASE VIDEO WRITER
    # ─────────────────────────────────────────────

    def _write_base_video(self, scenes, folder, niche):
        niche_style = NICHE_STYLES.get(niche, NICHE_STYLES["default"])
        grain_intensity = niche_style.get("film_grain", 0.1)

        print(
            f"🎨 Style: '{niche}' | grain: {grain_intensity} | Transitions: Human-Style Hard Cuts"
        )

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

            # 🟢 NEW: "Perfect Sync" Semantic Whisper Cuts
            clip_durations = []
            words = scene.get("whisper_words", [])
            trigger_words = scene.get("trigger_words", [])
            num_clips = len(visual_paths)

            # --- ADD THIS SAFETY CHECK BLOCK ---
            if not trigger_words or len(trigger_words) != num_clips:
                print(
                    f"      ⚠️ Missing or mismatched trigger words. Falling back to math cuts."
                )
                clip_durations = [duration / max(1, num_clips)] * num_clips
                # ... skip the rest of the semantic logic for this scene
                words = []  # Force it into the fallback `else` block below
            # -----------------------------------

            if words and num_clips > 1:
                cut_times = [
                    0.0
                ]  # First clip always starts at the beginning of the audio
                word_idx = 0

                # Scan Whisper timestamps to find the exact trigger words
                for c_idx in range(1, num_clips):
                    target = (
                        trigger_words[c_idx].lower().strip()
                        if c_idx < len(trigger_words)
                        else ""
                    )
                    target = "".join(e for e in target if e.isalnum())

                    found_time = None
                    if target:
                        for idx in range(word_idx, len(words)):
                            clean_w = "".join(
                                e for e in words[idx]["word"] if e.isalnum()
                            ).lower()

                            # Exact match or strong substring match (e.g. "ears" vs "ear")
                            if target == clean_w or (
                                len(target) > 3
                                and (target in clean_w or clean_w in target)
                            ):
                                found_time = words[idx]["start"]
                                word_idx = idx + 1  # Start the next search from here
                                break

                    if found_time is not None and found_time > cut_times[-1]:
                        cut_times.append(found_time)
                    else:
                        # Fallback: if AI hallucinated a word or Whisper missed it, do a math cut for the remaining time
                        remaining_clips = num_clips - c_idx
                        remaining_time = duration - cut_times[-1]
                        cut_times.append(
                            cut_times[-1] + (remaining_time / (remaining_clips + 1))
                        )

                # Convert cut timestamps into clip durations
                for c_idx in range(num_clips):
                    if c_idx == num_clips - 1:
                        clip_dur = duration - cut_times[c_idx]
                    else:
                        clip_dur = cut_times[c_idx + 1] - cut_times[c_idx]

                    # Safety fallback
                    clip_durations.append(max(0.5, clip_dur))
            else:
                # Fallback mathematical cut if Whisper fails completely
                clip_durations = [duration / max(1, num_clips)] * num_clips

            scene_clips = []
            scene_keywords = scene.get("keywords", [])

            for vi, raw_path in enumerate(visual_paths):
                path = os.path.normpath(os.path.join(PROJECT_ROOT, raw_path))
                keyword = scene_keywords[vi] if vi < len(scene_keywords) else ""

                # Pass the precise Whisper duration to the visual builder
                img_duration = clip_durations[vi]

                clip = self._make_clip(path, img_duration, niche_style, keyword=keyword)
                if clip is not None:
                    scene_clips.append(clip)

            if not scene_clips:
                audio_clip.close()
                continue

            # 🟢 Forced random transitions removed here in favor of clean hard cuts.

            if grain_intensity > 0:
                grained = []
                for clip in scene_clips:
                    gi = grain_intensity
                    grained.append(
                        clip.transform(
                            lambda gf, t, g=gi: self._apply_film_grain(gf(t), g),
                            apply_to="video",
                        )
                    )
                scene_clips = grained

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
                    logger=None,
                )
                scene_files.append(temp_path)
            except Exception as e:
                print(f"⚠️ Failed to write scene {i}: {e}")
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
            print("\n🚨 CRITICAL: No scene files were successfully written!")
            return None, None

        print("🔗 Concatenating temporary scene files into base video...")
        temp_clips = [VideoFileClip(p) for p in scene_files]
        base_video = concatenate_videoclips(temp_clips, method="chain")

        base_path = os.path.join(folder, "_BASE_VIDEO_TEMP.mp4")
        full_audio_path = os.path.join(folder, "FULL_AUDIO_TEMP.mp3")

        # 1. Write the CLEAN voiceover audio to disk so Whisper can read it later
        base_video.audio.write_audiofile(full_audio_path, logger=None)

        # 2. 🎵 Add Auto-Ducking Background Music
        final_audio = base_video.audio
        music_dir = os.path.join(PROJECT_ROOT, "assets", "music")

        if os.path.exists(music_dir):
            music_files = glob.glob(os.path.join(music_dir, "*.mp3")) + glob.glob(
                os.path.join(music_dir, "*.wav")
            )
            if music_files:
                bg_music_path = random.choice(music_files)
                print(
                    f"   🎵 Mixing background music: {os.path.basename(bg_music_path)}"
                )
                try:
                    bg_clip = AudioFileClip(bg_music_path)

                    # Loop the music if it's shorter than the video
                    if bg_clip.duration < base_video.duration:
                        bg_clip = afx.AudioLoop(duration=base_video.duration).apply(
                            bg_clip
                        )
                    else:
                        bg_clip = bg_clip.subclipped(0, base_video.duration)

                    # Audio Ducking: Drop music volume to 10% so the voice is crystal clear
                    bg_clip = afx.MultiplyVolume(0.10).apply(bg_clip)

                    # Mix the voiceover and the music track together
                    final_audio = CompositeAudioClip([base_video.audio, bg_clip])
                except Exception as e:
                    print(f"   ⚠️ Failed to mix background music: {e}")
            else:
                print("   ⚠️ 'assets/music' directory is empty. No music added.")
        else:
            print("   ⚠️ 'assets/music' directory not found. No music added.")

        # Attach the newly mixed audio to the video
        base_video = base_video.with_audio(final_audio)

        # 3. Write the final base video
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

    # ─────────────────────────────────────────────
    # CAPTION BURN-IN (English only, no Hindi skip)
    # ─────────────────────────────────────────────

    def _draw_text_on_video(self, base_path, full_audio_path, out_path, video_title):
        """
        Whisper transcribes the audio and burns word-by-word captions
        directly into the video frames. English only — Hindi skip removed.
        """
        self._load_whisper()
        print("📝 Processing audio for English captions...")
        result = self.model.transcribe(
            full_audio_path, word_timestamps=True, fp16=False
        )
        self._unload_whisper()

        words = []
        for segment in result["segments"]:
            for word in segment.get("words", []):
                words.append(word)

        if not words:
            print("⚠️ No word timestamps found. Skipping captions.")
            shutil.copy(base_path, out_path)
            return

        print("📝 Cleaning timestamps...")
        for i in range(len(words)):
            if i < len(words) - 1:
                words[i]["end"] = min(words[i]["end"], words[i + 1]["start"])

        # ── Cross-platform font resolution ──
        import sys

        FONT_CANDIDATES = []
        if sys.platform == "win32":
            FONT_CANDIDATES = [
                r"C:\Windows\Fonts\ariblk.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ]
        elif sys.platform == "darwin":
            FONT_CANDIDATES = [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/Library/Fonts/Arial Bold.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
        else:
            FONT_CANDIDATES = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            ]

        caption_font = None
        title_font = None
        for font_path in FONT_CANDIDATES:
            if os.path.exists(font_path):
                try:
                    caption_font = ImageFont.truetype(font_path, 90)
                    title_font = ImageFont.truetype(font_path, 80)
                    print(f"🔤 Font loaded: {font_path}")
                    break
                except Exception:
                    continue

        if caption_font is None:
            print("⚠️ No system font found — falling back to Pillow default.")
            caption_font = ImageFont.load_default()
            title_font = ImageFont.load_default()

        wrapped_title = "\n".join(textwrap.wrap(video_title, width=20))

        def draw_frame(get_frame, t):
            frame = get_frame(t)
            active_word = None
            for w in words:
                if w["start"] <= t <= w["end"] and w["end"] > w["start"]:
                    active_word = w["word"].strip().upper()
                    break
            show_title = t <= 3.0
            if not active_word and not show_title:
                return frame

            img = Image.fromarray(frame)
            draw = ImageDraw.Draw(img)

            if show_title:
                bbox = draw.multiline_textbbox((0, 0), wrapped_title, font=title_font)
                x = (1080 - (bbox[2] - bbox[0])) / 2
                draw.multiline_text(
                    (x, 300),
                    wrapped_title,
                    font=title_font,
                    fill="yellow",
                    stroke_width=5,
                    stroke_fill="black",
                    align="center",
                )

            if active_word:
                bbox = draw.textbbox((0, 0), active_word, font=caption_font)
                x = (1080 - (bbox[2] - bbox[0])) / 2
                draw.text(
                    (x, 1500),
                    active_word,
                    font=caption_font,
                    fill="white",
                    stroke_width=4,
                    stroke_fill="black",
                )

            return np.array(img)

        print("🎨 Burning text directly into video frames...")
        from moviepy import VideoFileClip as VFC

        base_video = VFC(base_path)
        final_video = base_video.transform(draw_frame)
        final_video = final_video.with_audio(base_video.audio)
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

    # ─────────────────────────────────────────────
    # CLEAN VIDEO CLIPS AND VOICE CLIPS
    # ─────────────────────────────────────────────
    def _cleanup_intermediate_files(
        self, folder_path, final_video_name="FINAL_VIDEO.mp4"
    ):
        """
        Deletes all downloaded raw visuals, audio segments, and temp renders.
        Keeps only the final video and metadata files.
        """
        print("      🧹 Cleaning up intermediate clips and audio files...")
        import time

        # Short pause to let Windows release any lingering MoviePy file locks
        time.sleep(2)

        deleted_files = 0

        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)

            if os.path.isfile(file_path):
                # Check if it is the final video OR a metadata file
                if filename == final_video_name or filename.startswith(
                    "FINAL_VIDEO_METADATA"
                ):
                    continue
                else:
                    try:
                        os.remove(file_path)
                        deleted_files += 1
                    except Exception as e:
                        print(
                            f"         ⚠️ Could not delete {filename} (might be locked by OS): {e}"
                        )

        print(
            f"      ✅ Cleanup complete. Deleted {deleted_files} raw files to save disk space."
        )

    # ─────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────

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

        # 🟢 NEW: Pre-analyze audio with Whisper for human-timed cuts
        self._load_whisper()
        print("🧠 Pre-analyzing audio for human-timed video cuts...")
        for scene in scenes:
            audio_path = os.path.normpath(
                os.path.join(PROJECT_ROOT, scene.get("audio_path", ""))
            )
            if os.path.exists(audio_path):
                res = self.model.transcribe(
                    audio_path, word_timestamps=True, fp16=False
                )
                words = []
                for segment in res.get("segments", []):
                    words.extend(segment.get("words", []))
                scene["whisper_words"] = words
        self._unload_whisper()

        # Load CLIP once for the whole assembly job
        self._load_clip()

        base_path, full_audio_path = self._write_base_video(scenes, folder, niche)

        # Free CLIP RAM before loading Whisper — both models must not overlap
        self._unload_clip()

        if not base_path:
            return

        out_path = os.path.join(folder, "FINAL_VIDEO.mp4")
        time.sleep(1)

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
        # 🟢 NEW: Trigger the cleanup of the raw downloaded files!
        self._cleanup_intermediate_files(folder)
        print("🧹 Cleaned up all temporary files.")
