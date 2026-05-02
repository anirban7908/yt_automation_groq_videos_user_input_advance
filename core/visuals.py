import os
import time
import requests
import random
import re
import json
import subprocess
from core.db_manager import DBManager
from dotenv import load_dotenv
from PIL import Image
import io
from groq import Groq
from core.ai_core import AIEngine
import concurrent.futures
import threading

load_dotenv()
# Safely fetch the max size from .env, fallback to 250 if missing or invalid
try:
    MAX_SIZE_MB = float(os.getenv("MAX_DOWNLOAD_SIZE_MB", 250))
except ValueError:
    print("⚠️ MAX_DOWNLOAD_SIZE_MB is invalid in .env. Defaulting to 250MB.")
    MAX_SIZE_MB = 250.0


def _convert_to_mp4(input_path, delete_original=True):
    """
    Converts any video file (mov, ogv, webm, avi, ogg) to mp4 using ffmpeg.
    Returns the new .mp4 path on success, or None on failure.
    The original file is deleted after successful conversion.
    """
    if input_path.endswith(".mp4"):
        return input_path

    output_path = os.path.splitext(input_path)[0] + ".mp4"
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                input_path,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        if (
            result.returncode == 0
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 10_000
        ):
            if delete_original and os.path.exists(input_path):
                os.remove(input_path)
            print(f"      🔄 Converted to MP4: {os.path.basename(output_path)}")
            return output_path
        else:
            print(f"      ⚠️ ffmpeg conversion failed for: {input_path}")
            return None
    except subprocess.TimeoutExpired:
        print(f"      ⚠️ ffmpeg conversion timed out for: {input_path}")
        return None
    except FileNotFoundError:
        print(
            f"      ⚠️ ffmpeg not found in PATH — cannot convert {os.path.basename(input_path)}"
        )
        return None
    except Exception as e:
        print(f"      ⚠️ Conversion error: {e}")
        return None


class VisualScout:
    def __init__(self, **kwargs):
        self.db = DBManager()
        self.unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY")
        self.pexels_key = os.getenv("PEXELS_API_KEY")
        self.pixabay_key = os.getenv("PIXABAY_API_KEY")
        self.ai = AIEngine()

        # Track downloaded clip IDs to prevent the same clip appearing twice
        self._used_pexels_ids = set()
        self._used_pixabay_ids = set()
        self._used_nasa_ids = set()
        self._used_wikimedia_titles = set()
        self._used_archive_ids = set()

        self.lock = threading.Lock()

    # ─────────────────────────────────────────────
    # NASA KEYWORD TRIGGER LIST
    # Any keyword containing one of these words
    # (case-insensitive, partial match) gets routed
    # to NASA *first* before falling back to Pexels.
    # ─────────────────────────────────────────────
    NASA_TRIGGERS = {
        "asteroid",
        "meteor",
        "meteorite",
        "comet",
        "nebula",
        "galaxy",
        "cosmos",
        "cosmic",
        "rocket",
        "spacecraft",
        "space shuttle",
        "shuttle",
        "iss",
        "space station",
        "mars",
        "moon",
        "lunar",
        "planet",
        "planetary",
        "saturn",
        "jupiter",
        "venus",
        "mercury",
        "uranus",
        "neptune",
        "solar flare",
        "solar storm",
        "sun corona",
        "corona",
        "sunspot",
        "black hole",
        "neutron star",
        "pulsar",
        "quasar",
        "dark matter",
        "hubble",
        "james webb",
        "webb telescope",
        "milky way",
        "orbit",
        "orbital",
        "astronaut",
        "spacewalk",
        "earth from space",
        "aurora borealis",
        "aurora australis",
        "northern lights",
        "supernova",
        "exoplanet",
        "deep space",
        "space debris",
        "launch pad",
        "launch",
        "capsule",
        "lander",
        "rover",
        "curiosity",
        "perseverance",
        "telescope",
        "observatory",
        "star cluster",
        "globular cluster",
        "interstellar",
        "galactic",
        "stellar",
        "dwarf star",
        "red giant",
        "space exploration",
        "nasa",
        "esa",
        "spacex",
        "space",
    }

    # ─────────────────────────────────────────────
    # NICHE → FALLBACK SOURCE ORDER
    # Used ONLY when keyword-level routing doesn't
    # match a specific source. This is the tiebreaker.
    # ─────────────────────────────────────────────
    # ─────────────────────────────────────────────
    # NICHE → FALLBACK SOURCE ORDER
    # ─────────────────────────────────────────────
    NICHE_SOURCE_ORDER = {
        "space": [
            "nasa",
            "pixabay_video",
            "pexels_video",
            "wikimedia",
            "unsplash_image",
            "internet_archive",  # Moved to bottom
        ],
        "tech_ai": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "pexels_image",
            "wikimedia",
        ],
        "psychology": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
            "internet_archive",  # Moved to bottom
        ],
        "health_wellness": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
            "pexels_image",
        ],
        "animals_nature": [
            "pixabay_video",
            "pexels_video",
            "unsplash_image",
            "wikimedia",
            "internet_archive",  # Moved to bottom
        ],
        "finance_economy": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
        ],
        "bizarre_facts": [
            "pexels_video",  # Pexels first, NOT internet archive
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
            "internet_archive",  # Moved to bottom
        ],
        "history_world": [
            "wikimedia",  # Wikimedia is better for history
            "pixabay_video",
            "pexels_video",
            "internet_archive",  # Moved to bottom
        ],
        "default": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
            "internet_archive",
        ],
    }
    # ─────────────────────────────────────────────
    # KEYWORD → SOURCE ROUTER
    # ─────────────────────────────────────────────

    def _route_source_order(self, keyword, niche):
        """
        Returns the ordered list of sources to try for this specific keyword.

        Logic:
          1. Check if the keyword contains any NASA trigger word
             → if yes, put "nasa" at the front of the list
          2. Otherwise fall back to the niche-level source order

        This means "asteroid" in a history_world video still hits NASA first,
        and "Lakota village" in a space video still hits Pexels first.
        """
        kw_lower = keyword.lower()

        # Check for NASA triggers (any substring match)
        is_nasa_keyword = any(trigger in kw_lower for trigger in self.NASA_TRIGGERS)

        if is_nasa_keyword:
            # NASA first, then standard video sources as fallback
            print(f"      🛰️  NASA route detected for: '{keyword}'")
            return [
                "nasa",
                "pixabay_video",
                "pexels_video",
                "pixabay_image",
                "unsplash_image",
                "pexels_image",
            ]

        # Standard niche-based order
        return self.NICHE_SOURCE_ORDER.get(niche, self.NICHE_SOURCE_ORDER["default"])

    # ─────────────────────────────────────────────
    # AI CANDIDATE SELECTOR
    # ─────────────────────────────────────────────

    def is_valid_image(self, content):
        try:
            img = Image.open(io.BytesIO(content))
            img.verify()
            return True
        except:
            return False

    def _ai_choose_best_visual(self, keyword, candidates, source_type):
        """
        Asks Groq (Llama 3.3) to pick the most relevant visual from a list of candidates.
        Returns the index of the chosen candidate, or 0 if the AI fails.
        """
        if not candidates or len(candidates) <= 1:
            return 0

        options_for_ai = []
        for i, c in enumerate(candidates):
            options_for_ai.append(
                {
                    "index": i,
                    "description": c.get("description", "No description available"),
                }
            )

        prompt = f"""
        TASK: You are an expert video editor. You must choose the best stock {source_type} for a scene about: "{keyword}".
        
        AVAILABLE OPTIONS:
        {json.dumps(options_for_ai, indent=2)}
        
        INSTRUCTIONS:
        1. Read the descriptions of the available {source_type}s.
        2. Pick the ONE index that best matches the physical subject "{keyword}".
        3. Output ONLY a valid JSON object containing the chosen 'index'.
        
        EXAMPLE OUTPUT:
        {{"index": 2}}
        """

        print(f"      🧠 AI reviewing {len(candidates)} {source_type} candidates...")
        try:
            response_text = self.ai.generate(
                system_prompt="You output ONLY valid JSON dictionaries.",
                user_prompt=prompt,
                require_json=True,
            )
            result = json.loads(response_text)
            chosen_index = int(result.get("index", 0))

            if 0 <= chosen_index < len(candidates):
                print(f"      🎯 AI selected option {chosen_index}.")
                return chosen_index

        except Exception as e:
            print(f"      ⚠️ AI Selection Failed ({e}). Falling back to option 0.")

        return 0

    # ─────────────────────────────────────────────
    # VIDEO SOURCES
    # ─────────────────────────────────────────────
    def _get_file_size_mb(self, url):
        """Pings the server to get the exact file size before downloading."""
        try:
            # stream=True reads only the headers, not the whole file
            res = requests.get(url, stream=True, timeout=5)
            size_bytes = int(res.headers.get("Content-Length", 0))
            res.close()
            return size_bytes / (1024 * 1024)
        except Exception:
            return 0

    def use_nasa_search(self, query, path):
        """
        NASA Image & Video Library — FREE, no API key required.
        Searches for videos first, falls back to images.
        Uses AI to select the most relevant result.
        Skips already-used NASA asset IDs to prevent duplicate clips.
        """
        print(f"      🚀 NASA Library: hunting for '{query}'...")
        try:
            search_url = (
                f"https://images-api.nasa.gov/search"
                f"?q={requests.utils.quote(query)}"
                f"&media_type=video"
                f"&page_size=10"
            )
            res = requests.get(search_url, timeout=15)
            if res.status_code != 200:
                return False

            items = res.json().get("collection", {}).get("items", [])

            # Fall back to images if no videos found
            if not items:
                search_url = search_url.replace("media_type=video", "media_type=image")
                res = requests.get(search_url, timeout=15)
                items = res.json().get("collection", {}).get("items", [])
                if not items:
                    return False

            # Filter out already-used NASA assets
            fresh_items = [
                item
                for item in items
                if item.get("data", [{}])[0].get("nasa_id", "")
                not in self._used_nasa_ids
            ]
            if not fresh_items:
                print(f"      ⚠️ All NASA results already used. Skipping.")
                return False

            # Build candidates for AI
            candidates = []
            for item in fresh_items[:5]:
                data = item.get("data", [{}])[0]
                candidates.append(
                    {
                        "description": f"Title: {data.get('title', '')}. Desc: {data.get('description', '')}",
                        "raw": item,
                    }
                )

            # Let AI choose
            chosen_index = self._ai_choose_best_visual(query, candidates, "NASA asset")
            item = candidates[chosen_index]["raw"]
            nasa_id = item.get("data", [{}])[0].get("nasa_id", "")

            asset_url = item["href"]
            asset_res = requests.get(asset_url, timeout=10)
            if asset_res.status_code != 200:
                return False

            links = asset_res.json()
            mp4_links = [l for l in links if l.endswith(".mp4")]
            jpg_links = [l for l in links if l.endswith(".jpg") or l.endswith(".jpeg")]

            if mp4_links:
                print(
                    f"      📏 Checking file sizes to maximize quality (Limit: 250MB)..."
                )

                # Order by highest quality to lowest
                priority_order = ["~orig", "~large", "~medium", "~small", "~mobile"]
                sorted_links = []
                for p in priority_order:
                    for l in mp4_links:
                        if p in l and l not in sorted_links:
                            sorted_links.append(l)

                # Append any others not caught by the priority list
                for l in mp4_links:
                    if l not in sorted_links:
                        sorted_links.append(l)

                best_link = None
                for link in sorted_links:
                    size_mb = self._get_file_size_mb(link)
                    if 0 < size_mb <= MAX_SIZE_MB:
                        best_link = link
                        print(
                            f"         ✅ Found optimal high-quality file: {size_mb:.1f} MB"
                        )
                        break

                # If everything is somehow over the limit, fall back to the smallest available
                if not best_link:
                    best_link = sorted_links[-1]
                    print(
                        f"         ⚠️ All files over {MAX_SIZE_MB}MB limit. Falling back to smallest available."
                    )

                print(f"      ⬇️ Downloading NASA video...")
                content = requests.get(best_link, timeout=120).content
                save_path = path.replace(".jpg", ".mp4")
                with open(save_path, "wb") as f:
                    f.write(content)
                self._used_nasa_ids.add(nasa_id)
                print(f"      ✅ NASA Video Secured. [ID: {nasa_id}]")
                return save_path

            elif jpg_links:
                content = requests.get(jpg_links[0], timeout=15).content
                if self.is_valid_image(content):
                    save_path = (
                        path if path.endswith(".jpg") else path.replace(".mp4", ".jpg")
                    )
                    with open(save_path, "wb") as f:
                        f.write(content)
                    self._used_nasa_ids.add(nasa_id)
                    print(f"      ✅ NASA Image Secured. [ID: {nasa_id}]")
                    return save_path

        except Exception as e:
            print(f"      ❌ NASA Search Failed: {e}")

        return False

    def use_pexels_video_search(self, query, path):
        """
        Search Pexels for portrait MP4 videos.
        Uses AI to select the best match.
        Skips already-used video IDs to prevent duplicate clips.
        """
        if not self.pexels_key:
            return False
        print(f"      🎥 Pexels Video: hunting for '{query}'...")
        try:
            url = (
                f"https://api.pexels.com/videos/search"
                f"?query={requests.utils.quote(query)}"
                f"&per_page=15"
                f"&orientation=portrait"
            )
            res = requests.get(
                url, headers={"Authorization": self.pexels_key}, timeout=30
            )

            videos = res.json().get("videos", [])
            if res.status_code == 200 and videos:
                # Filter out already-used video IDs
                fresh_videos = [
                    v for v in videos if v.get("id") not in self._used_pexels_ids
                ]
                if not fresh_videos:
                    print(f"      ⚠️ All Pexels results for '{query}' already used.")
                    return False

                # Build candidates for AI (max 5 fresh ones)
                candidates = []
                for v in fresh_videos[:5]:
                    candidates.append(
                        {
                            "description": v.get("url", "")
                            .split("/")[-2]
                            .replace("-", " "),
                            "raw": v,
                        }
                    )

                # Let AI choose
                chosen_index = self._ai_choose_best_visual(query, candidates, "video")
                chosen_video = candidates[chosen_index]["raw"]

                mp4_files = [
                    v
                    for v in chosen_video["video_files"]
                    if v["file_type"] == "video/mp4"
                ]

                if mp4_files:
                    print(
                        f"      📏 Checking Pexels file sizes to maximize quality (Limit: 250MB)..."
                    )

                    # Sort files by highest resolution first
                    mp4_files = sorted(
                        mp4_files,
                        key=lambda x: x.get("width", 0) * x.get("height", 0),
                        reverse=True,
                    )

                    best_file = None
                    for file in mp4_files:
                        size_mb = self._get_file_size_mb(file["link"])
                        if 0 < size_mb <= MAX_SIZE_MB:
                            best_file = file
                            resolution = f"{file.get('width')}x{file.get('height')}"
                            print(
                                f"         ✅ Found optimal high-quality file ({resolution}): {size_mb:.1f} MB"
                            )
                            break

                    # Fallback to lowest resolution if all are massively huge
                    if not best_file:
                        best_file = mp4_files[-1]
                        print(
                            f"         ⚠️ All files over {MAX_SIZE_MB}MB limit. Falling back to smallest available."
                        )

                    print(f"      ⬇️ Downloading Pexels video...")
                    content = requests.get(best_file["link"], timeout=120).content
                    with open(path, "wb") as f:
                        f.write(content)
                    self._used_pexels_ids.add(chosen_video["id"])
                    print(f"      ✅ Pexels Video Secured. [ID: {chosen_video['id']}]")
                    return path
        except Exception as e:
            print(f"      ❌ Pexels Video Failed: {e}")
        return False

    def use_pixabay_video_search(self, query, path):
        """
        Search Pixabay for MP4 videos.
        Uses AI to select the best match.
        Skips already-used video IDs to prevent duplicate clips.
        """
        if not self.pixabay_key:
            return False
        print(f"      🎥 Pixabay Video: hunting for '{query}'...")
        try:
            url = (
                f"https://pixabay.com/api/videos/"
                f"?key={self.pixabay_key}"
                f"&q={requests.utils.quote(query)}"
                f"&video_type=film&per_page=15"
            )
            res = requests.get(url, timeout=30)

            hits = res.json().get("hits", [])
            if res.status_code == 200 and hits:
                # Filter out already-used video IDs
                fresh_hits = [
                    h for h in hits if h.get("id") not in self._used_pixabay_ids
                ]
                if not fresh_hits:
                    print(f"      ⚠️ All Pixabay results for '{query}' already used.")
                    return False

                # Build candidates for AI
                candidates = []
                for hit in fresh_hits[:5]:
                    candidates.append({"description": hit.get("tags", ""), "raw": hit})

                # Let AI choose
                chosen_index = self._ai_choose_best_visual(query, candidates, "video")
                chosen_video = candidates[chosen_index]["raw"]

                videos = chosen_video.get("videos", {})
                for quality in ["large", "medium", "small", "tiny"]:
                    if quality in videos and videos[quality].get("url"):
                        content = requests.get(
                            videos[quality]["url"], timeout=60
                        ).content
                        with open(path, "wb") as f:
                            f.write(content)
                        self._used_pixabay_ids.add(chosen_video["id"])
                        print(
                            f"      ✅ Pixabay Video Secured ({quality}). [ID: {chosen_video['id']}]"
                        )
                        return path
        except Exception as e:
            print(f"      ❌ Pixabay Video Failed: {e}")
        return False

    # ─────────────────────────────────────────────
    # IMAGE SOURCES
    # ─────────────────────────────────────────────

    def use_unsplash_image_search(self, query, path):
        """Search Unsplash for portrait images and use AI to select the best match."""
        if not self.unsplash_key:
            return False
        print(f"      📸 Unsplash Image: hunting for '{query}'...")
        try:
            url = f"https://api.unsplash.com/search/photos?query={requests.utils.quote(query)}&per_page=5&orientation=portrait"
            headers = {"Authorization": f"Client-ID {self.unsplash_key}"}
            res = requests.get(url, headers=headers, timeout=30)

            results = res.json().get("results", [])
            if res.status_code == 200 and results:
                candidates = []
                for img in results[:5]:
                    desc = img.get("alt_description") or img.get(
                        "description", "No description"
                    )
                    candidates.append({"description": desc, "raw": img})

                chosen_index = self._ai_choose_best_visual(query, candidates, "image")
                chosen_image = candidates[chosen_index]["raw"]

                img_url = chosen_image["urls"]["regular"]
                content = requests.get(img_url, timeout=20).content
                if self.is_valid_image(content):
                    save_path = (
                        path if path.endswith(".jpg") else path.replace(".mp4", ".jpg")
                    )
                    with open(save_path, "wb") as f:
                        f.write(content)
                    print("      ✅ Unsplash Image Secured.")
                    return save_path
        except Exception as e:
            print(f"      ❌ Unsplash Image Failed: {e}")
        return False

    def use_pexels_image_search(self, query, path):
        """Search Pexels for portrait photos and use AI to select the best match."""
        if not self.pexels_key:
            return False
        print(f"      📸 Pexels Image: hunting for '{query}'...")
        try:
            url = f"https://api.pexels.com/v1/search?query={requests.utils.quote(query)}&per_page=5&orientation=portrait"
            res = requests.get(
                url, headers={"Authorization": self.pexels_key}, timeout=30
            )

            photos = res.json().get("photos", [])
            if res.status_code == 200 and photos:
                candidates = []
                for p in photos[:5]:
                    candidates.append(
                        {
                            "description": p.get(
                                "alt", p.get("url", "").split("/")[-2].replace("-", " ")
                            ),
                            "raw": p,
                        }
                    )

                chosen_index = self._ai_choose_best_visual(query, candidates, "image")
                chosen_image = candidates[chosen_index]["raw"]

                img_url = chosen_image["src"]["large"]
                content = requests.get(img_url, timeout=20).content
                if self.is_valid_image(content):
                    save_path = (
                        path if path.endswith(".jpg") else path.replace(".mp4", ".jpg")
                    )
                    with open(save_path, "wb") as f:
                        f.write(content)
                    print("      ✅ Pexels Image Secured.")
                    return save_path
        except Exception as e:
            print(f"      ❌ Pexels Image Failed: {e}")
        return False

    def use_pixabay_image_search(self, query, path):
        """Search Pixabay for photos and use AI to select the best match."""
        if not self.pixabay_key:
            return False
        print(f"      📸 Pixabay Image: hunting for '{query}'...")
        try:
            url = (
                f"https://pixabay.com/api/"
                f"?key={self.pixabay_key}"
                f"&q={requests.utils.quote(query)}"
                f"&image_type=photo&per_page=5"
            )
            res = requests.get(url, timeout=30)

            hits = res.json().get("hits", [])
            if res.status_code == 200 and hits:
                candidates = []
                for hit in hits[:5]:
                    candidates.append(
                        {"description": hit.get("tags", "No tags"), "raw": hit}
                    )

                chosen_index = self._ai_choose_best_visual(query, candidates, "image")
                chosen_image = candidates[chosen_index]["raw"]

                img_url = chosen_image.get(
                    "largeImageURL", chosen_image.get("webformatURL")
                )
                if img_url:
                    content = requests.get(img_url, timeout=20).content
                    if self.is_valid_image(content):
                        save_path = (
                            path
                            if path.endswith(".jpg")
                            else path.replace(".mp4", ".jpg")
                        )
                        with open(save_path, "wb") as f:
                            f.write(content)
                        print("      ✅ Pixabay Image Secured.")
                        return save_path
        except Exception as e:
            print(f"      ❌ Pixabay Image Failed: {e}")
        return False

    def use_internet_archive(self, query, path):
        """
        Internet Archive (archive.org) — free public domain media library.
        No API key required. Best for: historical newsreels, old documentaries,
        public domain films, archival NASA footage, nature films, science reels.

        Strategy:
          1. Search Archive.org full-text search API for video files
          2. Filter to actual downloadable video formats (mp4, ogv, avi)
          3. Use AI to pick the most relevant result from candidates
          4. Download the best quality file directly
          5. Skip already-used identifiers to prevent duplicates
        """
        print(f"      📼 Internet Archive: hunting for '{query}'...")
        try:
            # ── STEP 1: Search Archive.org ──
            search_url = "https://archive.org/advancedsearch.php"
            params = {
                "q": f"{query} AND mediatype:movies",
                "fl[]": ["identifier", "title", "description", "subject"],
                "rows": "10",
                "page": "1",
                "output": "json",
            }
            res = requests.get(
                search_url,
                params=params,
                timeout=15,
                headers={"User-Agent": "YTAutomationBot/1.0"},
            )
            if res.status_code != 200:
                print(f"      ⚠️ Internet Archive API returned {res.status_code}.")
                return False

            docs = res.json().get("response", {}).get("docs", [])
            if not docs:
                print(f"      ⚠️ No Internet Archive results for '{query}'.")
                return False

            # Filter out already-used identifiers
            fresh_docs = [
                d for d in docs if d.get("identifier") not in self._used_archive_ids
            ]
            if not fresh_docs:
                print(f"      ⚠️ All Archive.org results already used for '{query}'.")
                return False

            # ── STEP 2: Build candidates for AI ──
            candidates = []
            for doc in fresh_docs[:5]:
                desc = doc.get("description", "")
                if isinstance(desc, list):
                    desc = " ".join(desc)
                candidates.append(
                    {
                        "description": f"{doc.get('title', '')}. {str(desc)[:200]}",
                        "raw": doc,
                    }
                )

            # ── STEP 3: AI picks the best match ──
            chosen_index = self._ai_choose_best_visual(
                query, candidates, "archive film"
            )
            chosen = candidates[chosen_index]["raw"]
            identifier = chosen.get("identifier")

            # ── STEP 4: Resolve downloadable video file ──
            meta_url = f"https://archive.org/metadata/{identifier}"
            meta_res = requests.get(
                meta_url, timeout=15, headers={"User-Agent": "YTAutomationBot/1.0"}
            )
            if meta_res.status_code != 200:
                print(f"      ⚠️ Could not fetch metadata for '{identifier}'.")
                return False

            files = meta_res.json().get("files", [])

            # Prefer mp4 first, then other formats as fallback
            video_exts_priority = [".mp4", ".ogv", ".webm", ".avi", ".mov"]
            video_files = []
            for f in files:
                name = f.get("name", "").lower()
                size = int(f.get("size", 0))
                if (
                    any(name.endswith(ext) for ext in video_exts_priority)
                    and size > 100_000
                ):
                    video_files.append(f)

            if not video_files:
                print(f"      ⚠️ No downloadable video files found in '{identifier}'.")
                return False

            # 🟢 NEW: "Goldilocks" Size Filter (Target files between 1MB and 150MB)
            valid_files = [
                f
                for f in video_files
                if 1_000_000 < int(f.get("size", 0)) < 150_000_000
            ]

            # Fallback: If all files are massive, just grab the smallest one over 1MB
            if not valid_files:
                valid_files = [
                    f for f in video_files if int(f.get("size", 0)) > 1_000_000
                ]
                if not valid_files:
                    valid_files = video_files

            # Prefer mp4 files first
            mp4_files = [
                f for f in valid_files if f.get("name", "").lower().endswith(".mp4")
            ]

            # Sort ASCENDING (smallest first) instead of reverse=True
            best_file = sorted(
                mp4_files if mp4_files else valid_files,
                key=lambda f: int(f.get("size", 0)),
            )[0]

            file_name = best_file["name"]
            download_url = f"https://archive.org/download/{identifier}/{requests.utils.quote(file_name)}"

            # ── STEP 5: Download ──
            ext = os.path.splitext(file_name)[-1].lower()
            mp4_path = path if path.endswith(".mp4") else path.replace(".jpg", ".mp4")

            print(f"      ⬇️  Downloading: {file_name[:60]} from '{identifier}'...")
            content = requests.get(
                download_url, timeout=120, headers={"User-Agent": "YTAutomationBot/1.0"}
            ).content

            if len(content) < 50_000:
                print(f"      ⚠️ Downloaded file too small, likely an error page.")
                return False

            if ext == ".mp4":
                save_path = mp4_path
                with open(save_path, "wb") as f:
                    f.write(content)
            else:
                # Save with original extension, then convert to mp4
                raw_path = mp4_path.replace(".mp4", ext)
                with open(raw_path, "wb") as f:
                    f.write(content)
                converted = _convert_to_mp4(raw_path)
                if not converted:
                    print(f"      ⚠️ Could not convert {ext} to mp4. Skipping.")
                    return False
                save_path = converted

            self._used_archive_ids.add(identifier)
            print(f"      ✅ Internet Archive Video Secured. [{identifier}]")
            return save_path

        except Exception as e:
            print(f"      ❌ Internet Archive Failed: {e}")
            return False

    def use_wikimedia_search(self, query, path):
        """
        Wikimedia Commons — free, copyright-safe media via the official API.
        No API key required. Covers videos, images, scientific animations,
        historical footage, and documentary clips under CC0/CC-BY/public domain.

        Strategy:
          1. Search Commons API for files matching the query
          2. Filter to video (webm/ogv/mp4) first, fallback to image (jpg/png)
          3. Use AI to pick the most relevant result from up to 5 candidates
          4. Download directly from the Wikimedia file URL
          5. Skip already-used file titles to prevent duplicates
        """
        print(f"      🌐 Wikimedia Commons: hunting for '{query}'...")
        try:
            # ── STEP 1: Search the Commons API ──
            search_url = "https://commons.wikimedia.org/w/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": f"{query} filetype:bitmap|video",
                "srnamespace": "6",  # File namespace only
                "srlimit": "15",
                "srprop": "snippet|titlesnippet",
                "format": "json",
            }
            res = requests.get(
                search_url,
                params=params,
                timeout=15,
                headers={"User-Agent": "YTAutomationBot/1.0"},
            )
            if res.status_code != 200:
                print(f"      ⚠️ Wikimedia API returned {res.status_code}.")
                return False

            results = res.json().get("query", {}).get("search", [])
            if not results:
                print(f"      ⚠️ No Wikimedia results for '{query}'.")
                return False

            # ── STEP 2: Separate videos and images, skip used titles ──
            video_exts = (".webm", ".ogv", ".mp4", ".ogg")
            image_exts = (".jpg", ".jpeg", ".png", ".gif")

            video_candidates = []
            image_candidates = []

            for r in results:
                title = r.get("title", "")
                if title in self._used_wikimedia_titles:
                    continue
                tl = title.lower()
                snippet = re.sub(r"<[^>]+>", "", r.get("snippet", ""))
                entry = {"title": title, "description": snippet, "raw": r}
                if any(tl.endswith(ext) for ext in video_exts):
                    video_candidates.append(entry)
                elif any(tl.endswith(ext) for ext in image_exts):
                    image_candidates.append(entry)

            # Prefer videos; fall back to images
            candidates = video_candidates[:5] or image_candidates[:5]
            is_video = bool(video_candidates)

            if not candidates:
                print(f"      ⚠️ All Wikimedia results already used for '{query}'.")
                return False

            # ── STEP 3: AI picks the best candidate ──
            chosen_index = self._ai_choose_best_visual(
                query, candidates, "Wikimedia file"
            )
            chosen = candidates[chosen_index]
            chosen_title = chosen["title"]

            # ── STEP 4: Resolve the actual download URL via imageinfo API ──
            info_params = {
                "action": "query",
                "titles": chosen_title,
                "prop": "imageinfo",
                "iiprop": "url|mediatype|size",
                "format": "json",
            }
            info_res = requests.get(
                search_url,
                params=info_params,
                timeout=15,
                headers={"User-Agent": "YTAutomationBot/1.0"},
            )
            pages = info_res.json().get("query", {}).get("pages", {})
            file_url = None
            for page in pages.values():
                info = page.get("imageinfo", [{}])[0]
                file_url = info.get("url")
                break

            if not file_url:
                print(f"      ⚠️ Could not resolve download URL for '{chosen_title}'.")
                return False

            # ── STEP 5: Download ──
            print(f"      ⬇️  Downloading: {chosen_title[:60]}...")
            content = requests.get(
                file_url, timeout=60, headers={"User-Agent": "YTAutomationBot/1.0"}
            ).content

            if is_video:
                save_path = (
                    path if path.endswith(".mp4") else path.replace(".jpg", ".mp4")
                )
                # Wikimedia serves webm/ogv/ogg — save with original ext then convert
                ext = os.path.splitext(file_url)[-1].lower()
                if ext in (".webm", ".ogv", ".ogg", ".mov", ".avi"):
                    raw_path = save_path.replace(".mp4", ext)
                    with open(raw_path, "wb") as f:
                        f.write(content)
                    if os.path.getsize(raw_path) < 5_000:
                        print(f"      ⚠️ Downloaded file too small, skipping.")
                        os.remove(raw_path)
                        return False
                    # Convert to mp4 so assembler can read it
                    converted = _convert_to_mp4(raw_path)
                    if not converted:
                        print(f"      ⚠️ Could not convert {ext} to mp4. Skipping.")
                        return False
                    save_path = converted
                else:
                    with open(save_path, "wb") as f:
                        f.write(content)
                    if os.path.getsize(save_path) < 5_000:
                        print(f"      ⚠️ Downloaded file too small, skipping.")
                        os.remove(save_path)
                        return False
            else:
                save_path = (
                    path if path.endswith(".jpg") else path.replace(".mp4", ".jpg")
                )
                if not self.is_valid_image(content):
                    print(f"      ⚠️ Downloaded image is invalid.")
                    return False
                with open(save_path, "wb") as f:
                    f.write(content)
                if os.path.getsize(save_path) < 5_000:
                    print(f"      ⚠️ Downloaded file too small, skipping.")
                    os.remove(save_path)
                    return False

            self._used_wikimedia_titles.add(chosen_title)
            media_type = "Video" if is_video else "Image"
            print(f"      ✅ Wikimedia {media_type} Secured. [{chosen_title[:50]}]")
            return save_path

        except Exception as e:
            print(f"      ❌ Wikimedia Search Failed: {e}")
            return False

    def search_google_images(self, query, path):
        """Scrape Google Images as a last-resort fallback only."""
        print(f"      🌍 Google Image (last resort): hunting for '{query}'...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36"
        }
        try:
            url = f"https://www.google.com/search?q={requests.utils.quote(query)}&tbm=isch&udm=2"
            res = requests.get(url, headers=headers, timeout=10)
            matches = re.findall(r'"(https?://[^"]+?\.(?:jpg|jpeg|png))"', res.text)
            if matches:
                for img_url in matches[:5]:
                    try:
                        img_url = img_url.encode().decode("unicode_escape")
                        img_data = requests.get(
                            img_url, headers=headers, timeout=5
                        ).content
                        if self.is_valid_image(img_data):
                            with open(path, "wb") as f:
                                f.write(img_data)
                            print("      ✅ Google Image Secured.")
                            return path
                    except:
                        continue
        except Exception as e:
            print(f"      ❌ Google Image Failed: {e}")
        return False

    # ─────────────────────────────────────────────
    # SOURCE DISPATCHER
    # ─────────────────────────────────────────────

    def _try_source(self, source_name, query, base_filename, folder):
        """Calls the right API based on source_name. Returns saved path or False."""
        if source_name == "nasa":
            path = os.path.join(folder, base_filename + ".mp4")
            return self.use_nasa_search(query, path)
        elif source_name == "pexels_video":
            path = os.path.join(folder, base_filename + ".mp4")
            return self.use_pexels_video_search(query, path)
        elif source_name == "pixabay_video":
            path = os.path.join(folder, base_filename + ".mp4")
            return self.use_pixabay_video_search(query, path)
        elif source_name == "unsplash_image":
            path = os.path.join(folder, base_filename + ".jpg")
            return self.use_unsplash_image_search(query, path)
        elif source_name == "pixabay_image":
            path = os.path.join(folder, base_filename + ".jpg")
            return self.use_pixabay_image_search(query, path)
        elif source_name == "pexels_image":
            path = os.path.join(folder, base_filename + ".jpg")
            return self.use_pexels_image_search(query, path)
        elif source_name == "internet_archive":
            path = os.path.join(folder, base_filename + ".mp4")
            return self.use_internet_archive(query, path)
        elif source_name == "wikimedia":
            path = os.path.join(folder, base_filename + ".mp4")
            return self.use_wikimedia_search(query, path)
        return False

    # ─────────────────────────────────────────────
    # MAIN DOWNLOAD ORCHESTRATOR
    # ─────────────────────────────────────────────

    def _fetch_single_visual(self, kw, niche, base_filename, folder, all_keywords):
        """Worker function to fetch a single visual. Runs in parallel."""
        print(f"   🔍 Hunting for: '{kw}'...")
        saved_path = None

        # 1. Primary Attempt
        source_order = self._route_source_order(kw, niche)
        for source_name in source_order:
            result = self._try_source(source_name, kw, base_filename, folder)
            if result:
                saved_path = result
                print(f"      📦 [{kw}] Secured via {source_name}")
                break

        # 2. Fallback 1: Alt keywords
        if not saved_path:
            for fallback_kw in all_keywords:
                if fallback_kw == kw:
                    continue
                fallback_order = self._route_source_order(fallback_kw, niche)
                for source_name in fallback_order[:3]:
                    result = self._try_source(
                        source_name, fallback_kw, base_filename, folder
                    )
                    if result:
                        saved_path = result
                        print(
                            f"      📦 [{kw}] Fallback secured via {source_name} (alt: '{fallback_kw}')"
                        )
                        break
                if saved_path:
                    break

        # 3. Fallback 2: Simplified noun
        if not saved_path:
            simplified = kw.split()[0] if kw.split() else kw
            if simplified != kw:
                simple_order = self._route_source_order(simplified, niche)
                for source_name in simple_order[:3]:
                    result = self._try_source(
                        source_name, simplified, base_filename, folder
                    )
                    if result:
                        saved_path = result
                        print(
                            f"      📦 [{kw}] Simplified fallback secured via {source_name} ('{simplified}')"
                        )
                        break

        # 4. Fallback 3 & 4: Wikimedia / Google
        if not saved_path:
            path_mp4 = os.path.join(folder, base_filename + ".mp4")
            result = self.use_wikimedia_search(kw, path_mp4)
            if result:
                saved_path = result
            else:
                path_jpg = os.path.join(folder, base_filename + ".jpg")
                result = self.search_google_images(kw, path_jpg)
                if result:
                    saved_path = result

        # 5. Last resort: Black placeholder
        if not saved_path:
            print(f"      ❌ [{kw}] All sources exhausted. Using placeholder.")
            path_jpg = os.path.join(folder, base_filename + ".jpg")
            Image.new("RGB", (1080, 1920), (10, 10, 10)).save(path_jpg)
            saved_path = path_jpg

        return saved_path

    def download_visuals(self):
        task = self.db.collection.find_one({"status": "voiced"})
        if not task:
            return

        scenes = task.get("script_data", [])
        folder = task["folder_path"]
        niche = task.get("niche", "default").lower()

        # Reset duplicate guards
        self._used_pexels_ids = set()
        self._used_pixabay_ids = set()
        self._used_nasa_ids = set()
        self._used_wikimedia_titles = set()
        self._used_archive_ids = set()

        print(f"🎬 Visual Scout active | niche='{niche}' | {len(scenes)} scenes")
        print(f"⚡ Parallel processing enabled. Fetching assets simultaneously...")

        # Pre-allocate paths array so we can insert results out of order
        for scene in scenes:
            count = scene.get("image_count", 1)
            scene["image_paths"] = [None] * count

        # Use ThreadPoolExecutor to run all downloads at the exact same time
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_position = {}

            for i, scene in enumerate(scenes):
                video_title_fallback = task.get("title", "Breaking News")
                keywords = scene.get("keywords", [video_title_fallback])
                count = scene.get("image_count", 1)

                for j in range(count):
                    kw = keywords[j % len(keywords)]
                    base_filename = f"scene_{i}_visual_{j}"

                    # Submit the job to the parallel worker pool
                    future = executor.submit(
                        self._fetch_single_visual,
                        kw,
                        niche,
                        base_filename,
                        folder,
                        keywords,
                    )
                    future_to_position[future] = (i, j)

            # Collect results as they finish
            for future in concurrent.futures.as_completed(future_to_position):
                i, j = future_to_position[future]
                try:
                    saved_path = future.result()
                    scenes[i]["image_paths"][j] = saved_path
                except Exception as e:
                    print(f"      ❌ Thread crashed on scene {i} visual {j}: {e}")

        self.db.collection.update_one(
            {"_id": task["_id"]},
            {"$set": {"script_data": scenes, "status": "ready_to_assemble"}},
        )
        print("\n✅ All Visuals Secured at warp speed.")
