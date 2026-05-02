import os
import time
import requests
import random
import re
import json
import subprocess
import asyncio
from pathlib import Path
from core.db_manager import DBManager
from dotenv import load_dotenv
from PIL import Image
import io
from core.ai_core import AIEngine

load_dotenv()
try:
    MAX_SIZE_MB = float(os.getenv("MAX_DOWNLOAD_SIZE_MB", 250))
except ValueError:
    print("⚠️ MAX_DOWNLOAD_SIZE_MB is invalid in .env. Defaulting to 250MB.")
    MAX_SIZE_MB = 250.0


def _convert_to_mp4(input_path, delete_original=True):
    """Safely converts video to mp4, ensuring no ffmpeg zombies are left behind."""
    if input_path.endswith(".mp4"):
        return input_path

    output_path = str(Path(input_path).with_suffix(".mp4"))
    process = None
    try:
        process = subprocess.Popen(
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
        )
        process.communicate(timeout=120)

        if (
            process.returncode == 0
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
        if process:
            process.kill()
            process.communicate()  # Reap the zombie process
        print(f"      ⚠️ ffmpeg conversion timed out (killed) for: {input_path}")
        return None
    except Exception as e:
        print(f"      ⚠️ Conversion error: {e}")
        return None


def _download_chunked_sync(url, save_path, headers=None):
    """Downloads large files directly to disk in 64KB chunks to prevent RAM OOM spikes."""
    try:
        with requests.get(url, stream=True, timeout=60, headers=headers) as r:
            r.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return save_path
    except Exception as e:
        print(f"      ⚠️ Download failed: {e}")
        if os.path.exists(save_path):
            os.remove(save_path)
        return None


class VisualScout:
    def __init__(self, **kwargs):
        self.db = DBManager()
        self.unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY")
        self.pexels_key = os.getenv("PEXELS_API_KEY")
        self.pixabay_key = os.getenv("PIXABAY_API_KEY")
        self.ai = AIEngine()

        self._used_pexels_ids = set()
        self._used_pixabay_ids = set()
        self._used_nasa_ids = set()
        self._used_wikimedia_titles = set()
        self._used_archive_ids = set()

        # Prevents Groq 429 Rate Limits by allowing max 3 concurrent LLM evaluations
        self.ai_semaphore = asyncio.Semaphore(3)

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

    NICHE_SOURCE_ORDER = {
        "space": [
            "nasa",
            "pixabay_video",
            "pexels_video",
            "wikimedia",
            "unsplash_image",
            "internet_archive",
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
            "internet_archive",
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
            "internet_archive",
        ],
        "finance_economy": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
        ],
        "bizarre_facts": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
            "internet_archive",
        ],
        "history_world": [
            "wikimedia",
            "pixabay_video",
            "pexels_video",
            "internet_archive",
        ],
        "default": [
            "pexels_video",
            "pixabay_video",
            "unsplash_image",
            "wikimedia",
            "internet_archive",
        ],
    }

    def _route_source_order(self, keyword, niche):
        kw_lower = keyword.lower()
        if any(trigger in kw_lower for trigger in self.NASA_TRIGGERS):
            print(f"      🛰️  NASA route detected for: '{keyword}'")
            return [
                "nasa",
                "pixabay_video",
                "pexels_video",
                "pixabay_image",
                "unsplash_image",
                "pexels_image",
            ]
        return self.NICHE_SOURCE_ORDER.get(niche, self.NICHE_SOURCE_ORDER["default"])

    def is_valid_image(self, path):
        try:
            img = Image.open(path)
            img.verify()
            return True
        except:
            return False

    async def _ai_choose_best_visual(self, keyword, candidates, source_type):
        if not candidates or len(candidates) <= 1:
            return 0

        options_for_ai = [
            {"index": i, "description": c.get("description", "N/A")}
            for i, c in enumerate(candidates)
        ]
        prompt = f"""
        TASK: Choose the best stock {source_type} for a scene about: "{keyword}".
        OPTIONS: {json.dumps(options_for_ai)}
        Output ONLY a valid JSON object containing the chosen 'index'.
        EXAMPLE: {{"index": 2}}
        """

        async with self.ai_semaphore:
            try:
                # Utilizes the new AsyncGroq method to prevent event loop blocking
                response_text = await self.ai.agenerate(
                    system_prompt="You output ONLY valid JSON dictionaries.",
                    user_prompt=prompt,
                    require_json=True,
                )
                result = json.loads(response_text)
                chosen_index = int(result.get("index", 0))
                if 0 <= chosen_index < len(candidates):
                    return chosen_index
            except Exception as e:
                pass
        return 0

    def _get_file_size_mb(self, url):
        try:
            res = requests.head(url, timeout=5, allow_redirects=True)
            size_bytes = int(res.headers.get("Content-Length", 0))
            return size_bytes / (1024 * 1024)
        except:
            return 0

    async def _download_file(self, url, save_path, headers=None):
        """Async wrapper for the chunked downloader."""
        return await asyncio.to_thread(_download_chunked_sync, url, save_path, headers)

    async def use_nasa_search(self, query, path):
        try:
            search_url = f"https://images-api.nasa.gov/search?q={requests.utils.quote(query)}&media_type=video&page_size=10"
            res = await asyncio.to_thread(requests.get, search_url, timeout=15)
            items = res.json().get("collection", {}).get("items", [])

            if not items:
                search_url = search_url.replace("media_type=video", "media_type=image")
                res = await asyncio.to_thread(requests.get, search_url, timeout=15)
                items = res.json().get("collection", {}).get("items", [])

            fresh_items = [
                i
                for i in items
                if i.get("data", [{}])[0].get("nasa_id", "") not in self._used_nasa_ids
            ]
            if not fresh_items:
                return False

            candidates = [
                {"description": i.get("data", [{}])[0].get("description", ""), "raw": i}
                for i in fresh_items[:5]
            ]
            chosen_idx = await self._ai_choose_best_visual(
                query, candidates, "NASA asset"
            )
            item = candidates[chosen_idx]["raw"]
            nasa_id = item.get("data", [{}])[0].get("nasa_id", "")

            asset_res = await asyncio.to_thread(requests.get, item["href"], timeout=10)
            links = asset_res.json()

            mp4_links = [l for l in links if l.endswith(".mp4")]
            if mp4_links:
                best_link = next(
                    (
                        l
                        for l in ["~orig", "~large", "~medium", "~small", "~mobile"]
                        if any(l in x for x in mp4_links)
                    ),
                    mp4_links[0],
                )
                for link in mp4_links:
                    if any(p in link for p in ["~orig", "~large", "~medium"]):
                        if (
                            0
                            < await asyncio.to_thread(self._get_file_size_mb, link)
                            <= MAX_SIZE_MB
                        ):
                            best_link = link
                            break

                save_path = str(Path(path).with_suffix(".mp4"))
                if await self._download_file(best_link, save_path):
                    self._used_nasa_ids.add(nasa_id)
                    return save_path

        except Exception as e:
            print(f"      ❌ NASA Search Failed: {e}")
        return False

    async def use_pexels_video_search(self, query, path):
        if not self.pexels_key:
            return False
        try:
            url = f"https://api.pexels.com/videos/search?query={requests.utils.quote(query)}&per_page=15&orientation=portrait"
            res = await asyncio.to_thread(
                requests.get,
                url,
                headers={"Authorization": self.pexels_key},
                timeout=30,
            )
            videos = res.json().get("videos", [])

            fresh_videos = [
                v for v in videos if v.get("id") not in self._used_pexels_ids
            ]
            if not fresh_videos:
                return False

            candidates = [
                {
                    "description": v.get("url", "").split("/")[-2].replace("-", " "),
                    "raw": v,
                }
                for v in fresh_videos[:5]
            ]
            chosen_idx = await self._ai_choose_best_visual(query, candidates, "video")
            chosen_video = candidates[chosen_idx]["raw"]

            mp4_files = sorted(
                [
                    v
                    for v in chosen_video["video_files"]
                    if v["file_type"] == "video/mp4"
                ],
                key=lambda x: x.get("width", 0) * x.get("height", 0),
                reverse=True,
            )

            best_file = mp4_files[-1]
            for file in mp4_files:
                size = await asyncio.to_thread(self._get_file_size_mb, file["link"])
                if 0 < size <= MAX_SIZE_MB:
                    best_file = file
                    break

            # 🟢 THE FIX: Append .mp4 extension before downloading
            save_path = str(Path(path).with_suffix(".mp4"))
            if await self._download_file(best_file["link"], save_path):
                self._used_pexels_ids.add(chosen_video["id"])
                return save_path
        except Exception as e:
            print(f"      ❌ Pexels Video Failed: {e}")
        return False

    async def use_pixabay_video_search(self, query, path):
        if not self.pixabay_key:
            return False
        try:
            url = f"https://pixabay.com/api/videos/?key={self.pixabay_key}&q={requests.utils.quote(query)}&video_type=film&per_page=15"
            res = await asyncio.to_thread(requests.get, url, timeout=30)
            hits = res.json().get("hits", [])

            fresh_hits = [h for h in hits if h.get("id") not in self._used_pixabay_ids]
            if not fresh_hits:
                return False

            candidates = [
                {"description": h.get("tags", ""), "raw": h} for h in fresh_hits[:5]
            ]
            chosen_idx = await self._ai_choose_best_visual(query, candidates, "video")
            chosen_video = candidates[chosen_idx]["raw"]

            videos = chosen_video.get("videos", {})
            for quality in ["large", "medium", "small", "tiny"]:
                if quality in videos and videos[quality].get("url"):

                    # 🟢 THE FIX: Append .mp4 extension before downloading
                    save_path = str(Path(path).with_suffix(".mp4"))
                    if await self._download_file(videos[quality]["url"], save_path):
                        self._used_pixabay_ids.add(chosen_video["id"])
                        return save_path
        except Exception:
            pass
        return False

    async def use_internet_archive(self, query, path):
        try:
            search_url = "https://archive.org/advancedsearch.php"
            params = {
                "q": f"{query} AND mediatype:movies",
                "fl[]": ["identifier", "title", "description"],
                "rows": "10",
                "output": "json",
            }
            res = await asyncio.to_thread(
                requests.get, search_url, params=params, timeout=15
            )
            docs = res.json().get("response", {}).get("docs", [])

            fresh_docs = [
                d for d in docs if d.get("identifier") not in self._used_archive_ids
            ]
            if not fresh_docs:
                return False

            candidates = [
                {
                    "description": f"{d.get('title', '')}. {d.get('description', '')[:200]}",
                    "raw": d,
                }
                for d in fresh_docs[:5]
            ]
            chosen_idx = await self._ai_choose_best_visual(
                query, candidates, "archive film"
            )
            identifier = candidates[chosen_idx]["raw"].get("identifier")

            meta_url = f"https://archive.org/metadata/{identifier}"
            meta_res = await asyncio.to_thread(requests.get, meta_url, timeout=15)
            files = meta_res.json().get("files", [])

            video_files = [
                f
                for f in files
                if any(
                    f.get("name", "").lower().endswith(ext)
                    for ext in [".mp4", ".ogv", ".webm", ".avi", ".mov"]
                )
                and int(f.get("size", 0)) > 100_000
            ]
            if not video_files:
                return False

            valid_files = [
                f
                for f in video_files
                if 1_000_000 < int(f.get("size", 0)) < MAX_SIZE_MB * 1024 * 1024
            ]
            best_file = sorted(
                valid_files if valid_files else video_files,
                key=lambda f: int(f.get("size", 0)),
                reverse=True,
            )[0]

            download_url = f"https://archive.org/download/{identifier}/{requests.utils.quote(best_file['name'])}"
            ext = os.path.splitext(best_file["name"])[-1].lower()

            mp4_path = str(Path(path).with_suffix(".mp4"))
            raw_path = str(Path(path).with_suffix(ext)) if ext != ".mp4" else mp4_path

            if await self._download_file(download_url, raw_path):
                if ext != ".mp4":
                    converted = await asyncio.to_thread(_convert_to_mp4, raw_path)
                    if not converted:
                        return False
                    mp4_path = converted
                self._used_archive_ids.add(identifier)
                return mp4_path

        except Exception as e:
            print(f"      ❌ Internet Archive Failed: {e}")
        return False

    async def use_wikimedia_search(self, query, path):
        try:
            params = {
                "action": "query",
                "list": "search",
                "srsearch": f"{query} filetype:bitmap|video",
                "srnamespace": "6",
                "format": "json",
            }
            res = await asyncio.to_thread(
                requests.get,
                "https://commons.wikimedia.org/w/api.php",
                params=params,
                timeout=15,
            )
            results = res.json().get("query", {}).get("search", [])

            candidates = [
                {
                    "description": re.sub(r"<[^>]+>", "", r.get("snippet", "")),
                    "title": r.get("title", ""),
                    "raw": r,
                }
                for r in results
                if r.get("title") not in self._used_wikimedia_titles
            ][:5]
            if not candidates:
                return False

            chosen_idx = await self._ai_choose_best_visual(
                query, candidates, "Wikimedia file"
            )
            chosen_title = candidates[chosen_idx]["title"]

            info_params = {
                "action": "query",
                "titles": chosen_title,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            }
            info_res = await asyncio.to_thread(
                requests.get,
                "https://commons.wikimedia.org/w/api.php",
                params=info_params,
                timeout=15,
            )
            pages = info_res.json().get("query", {}).get("pages", {})
            file_url = list(pages.values())[0].get("imageinfo", [{}])[0].get("url")

            if not file_url:
                return False

            is_video = any(
                file_url.lower().endswith(ext)
                for ext in [".webm", ".ogv", ".mp4", ".ogg", ".mov", ".avi"]
            )
            save_path = str(Path(path).with_suffix(".mp4" if is_video else ".jpg"))
            raw_path = (
                str(Path(save_path).with_suffix(os.path.splitext(file_url)[-1].lower()))
                if is_video
                else save_path
            )

            if await self._download_file(
                file_url, raw_path, headers={"User-Agent": "YTAutomationBot/1.0"}
            ):
                if is_video and raw_path != save_path:
                    save_path = await asyncio.to_thread(_convert_to_mp4, raw_path)
                    if not save_path:
                        return False
                self._used_wikimedia_titles.add(chosen_title)
                return save_path
        except Exception:
            pass
        return False

    async def use_unsplash_image_search(self, query, path):
        if not self.unsplash_key:
            return False
        try:
            url = f"https://api.unsplash.com/search/photos?query={requests.utils.quote(query)}&per_page=5&orientation=portrait"
            res = await asyncio.to_thread(
                requests.get,
                url,
                headers={"Authorization": f"Client-ID {self.unsplash_key}"},
                timeout=30,
            )
            results = res.json().get("results", [])
            if results:
                candidates = [
                    {"description": img.get("alt_description", ""), "raw": img}
                    for img in results[:5]
                ]
                chosen_idx = await self._ai_choose_best_visual(
                    query, candidates, "image"
                )
                img_url = candidates[chosen_idx]["raw"]["urls"]["regular"]
                save_path = str(Path(path).with_suffix(".jpg"))
                if await self._download_file(img_url, save_path):
                    if await asyncio.to_thread(self.is_valid_image, save_path):
                        return save_path
        except Exception:
            pass
        return False

    async def use_pixabay_image_search(self, query, path):
        if not self.pixabay_key:
            return False
        try:
            url = f"https://pixabay.com/api/?key={self.pixabay_key}&q={requests.utils.quote(query)}&image_type=photo&per_page=5"
            res = await asyncio.to_thread(requests.get, url, timeout=30)
            hits = res.json().get("hits", [])
            if hits:
                candidates = [
                    {"description": hit.get("tags", ""), "raw": hit} for hit in hits[:5]
                ]
                chosen_idx = await self._ai_choose_best_visual(
                    query, candidates, "image"
                )
                img_url = candidates[chosen_idx]["raw"].get(
                    "largeImageURL", candidates[chosen_idx]["raw"].get("webformatURL")
                )
                if img_url:
                    save_path = str(Path(path).with_suffix(".jpg"))
                    if await self._download_file(img_url, save_path):
                        if await asyncio.to_thread(self.is_valid_image, save_path):
                            return save_path
        except Exception:
            pass
        return False

    async def use_pexels_image_search(self, query, path):
        if not self.pexels_key:
            return False
        try:
            url = f"https://api.pexels.com/v1/search?query={requests.utils.quote(query)}&per_page=5&orientation=portrait"
            res = await asyncio.to_thread(
                requests.get,
                url,
                headers={"Authorization": self.pexels_key},
                timeout=30,
            )
            photos = res.json().get("photos", [])
            if photos:
                candidates = [
                    {"description": p.get("alt", ""), "raw": p} for p in photos[:5]
                ]
                chosen_idx = await self._ai_choose_best_visual(
                    query, candidates, "image"
                )
                img_url = candidates[chosen_idx]["raw"]["src"]["large"]
                save_path = str(Path(path).with_suffix(".jpg"))
                if await self._download_file(img_url, save_path):
                    if await asyncio.to_thread(self.is_valid_image, save_path):
                        return save_path
        except Exception:
            pass
        return False

    async def _try_source(self, source_name, query, base_filename, folder):
        path = os.path.join(folder, base_filename)
        if source_name == "nasa":
            return await self.use_nasa_search(query, path)
        elif source_name == "pexels_video":
            return await self.use_pexels_video_search(query, path)
        elif source_name == "pixabay_video":
            return await self.use_pixabay_video_search(query, path)
        elif source_name == "unsplash_image":
            return await self.use_unsplash_image_search(query, path)
        elif source_name == "pixabay_image":
            return await self.use_pixabay_image_search(query, path)
        elif source_name == "pexels_image":
            return await self.use_pexels_image_search(query, path)
        elif source_name == "internet_archive":
            return await self.use_internet_archive(query, path)
        elif source_name == "wikimedia":
            return await self.use_wikimedia_search(query, path)
        return False

    async def _fetch_single_visual(
        self, kw, niche, base_filename, folder, all_keywords
    ):
        print(f"   🔍 Hunting for: '{kw}'...")
        saved_path = None

        source_order = self._route_source_order(kw, niche)
        for source_name in source_order:
            result = await self._try_source(source_name, kw, base_filename, folder)
            if result:
                print(f"      📦 [{kw}] Secured via {source_name}")
                return result

        # Fallback 1: Alt keywords
        for fallback_kw in all_keywords:
            if fallback_kw == kw:
                continue
            fallback_order = self._route_source_order(fallback_kw, niche)
            for source_name in fallback_order[:3]:
                result = await self._try_source(
                    source_name, fallback_kw, base_filename, folder
                )
                if result:
                    print(
                        f"      📦 [{kw}] Fallback secured via {source_name} (alt: '{fallback_kw}')"
                    )
                    return result

        # Fallback 2: Simplified noun
        simplified = kw.split()[0] if kw.split() else kw
        if simplified != kw:
            simple_order = self._route_source_order(simplified, niche)
            for source_name in simple_order[:3]:
                result = await self._try_source(
                    source_name, simplified, base_filename, folder
                )
                if result:
                    print(
                        f"      📦 [{kw}] Simplified fallback secured via {source_name}"
                    )
                    return result

        # Fallback 3: Wikimedia (Removed fragile Google Images scraper)
        result = await self.use_wikimedia_search(
            kw, os.path.join(folder, base_filename)
        )
        if result:
            return result

        # Last resort: Placeholder
        print(f"      ❌ [{kw}] All sources exhausted. Using placeholder.")
        path_jpg = str(Path(os.path.join(folder, base_filename)).with_suffix(".jpg"))
        await asyncio.to_thread(
            Image.new("RGB", (1080, 1920), (20, 20, 20)).save, path_jpg
        )
        return path_jpg

    async def _download_visuals_async(self, scenes, folder, niche):
        """Asynchronous execution engine for downloading all visuals."""
        tasks = []
        future_to_position = {}

        for i, scene in enumerate(scenes):
            keywords = scene.get("keywords", ["Breaking News"])
            count = scene.get("image_count", 1)
            scene["image_paths"] = [None] * count

            for j in range(count):
                kw = keywords[j % len(keywords)]
                base_filename = f"scene_{i}_visual_{j}"

                # Create asyncio tasks instead of threads
                task = asyncio.create_task(
                    self._fetch_single_visual(
                        kw, niche, base_filename, folder, keywords
                    )
                )
                tasks.append(task)
                future_to_position[task] = (i, j)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for task, result in zip(tasks, results):
            i, j = future_to_position[task]
            if isinstance(result, Exception):
                print(f"      ❌ Async Task crashed on scene {i} visual {j}: {result}")
            else:
                scenes[i]["image_paths"][j] = result

        return scenes

    def download_visuals(self):
        """Synchronous wrapper to maintain compatibility with main.py"""
        task = self.db.collection.find_one({"status": "voiced"})
        if not task:
            return

        scenes = task.get("script_data", [])
        folder = task["folder_path"]
        niche = task.get("niche", "default").lower()

        self._used_pexels_ids.clear()
        self._used_pixabay_ids.clear()
        self._used_nasa_ids.clear()
        self._used_wikimedia_titles.clear()
        self._used_archive_ids.clear()

        print(f"🎬 Visual Scout active | niche='{niche}' | {len(scenes)} scenes")
        print(f"⚡ Async processing enabled. Fetching assets simultaneously...")

        # Run the async engine synchronously
        updated_scenes = asyncio.run(
            self._download_visuals_async(scenes, folder, niche)
        )

        self.db.collection.update_one(
            {"_id": task["_id"]},
            {"$set": {"script_data": updated_scenes, "status": "ready_to_assemble"}},
        )
        print("\n✅ All Visuals Secured at warp speed without RAM spikes.")
