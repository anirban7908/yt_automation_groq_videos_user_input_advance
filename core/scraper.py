import requests
import feedparser
import random
import datetime
import re
import json
import os
import html
import concurrent.futures
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt
from core.db_manager import DBManager
from core.ai_core import AIEngine
from dotenv import load_dotenv

load_dotenv()


class NewsScraper:
    def __init__(self, **kwargs):
        self.db = DBManager()
        self.ai = AIEngine()

        # Pre-compile regex for RSS garbage removal (CPU optimization)
        garbage_phrases = [
            "See All",
            "email digest",
            "homepage feed",
            "native ad",
            "All Rights Reserved",
            "Posts from this topic",
            "Posts from this author",
            "A free daily digest of the news",
            "This is the title for the native ad",
        ]
        # Creates a single regex pattern like: (See All|email digest|...)
        self.garbage_regex = re.compile(
            "|".join(map(re.escape, garbage_phrases)), re.IGNORECASE
        )

        self.MASTER_NICHES = {
            "war_news": {
                "rss_feeds": [
                    "https://www.aljazeera.com/xml/rss/all.xml",
                    "https://www.defensenews.com/arc/outboundfeeds/rss/",
                    "https://www.defenseone.com/rss/all/",
                    "https://warontherocks.com/feed/",
                ],
                "pexels_style": "documentary",
                "hashtags": "#WarNews #GlobalConflict #Military #BreakingNews #Geopolitics",
                "voice": "en-US-ChristopherNeural",
            },
            "space": {
                "rss_feeds": [
                    "https://www.space.com/feeds/all",
                    "https://universetoday.com/feed",
                    "https://phys.org/rss-feed/space-news/",
                    "https://www.nasa.gov/feeds/iotd-feed/",
                    "https://spacenews.com/feed/",
                    "https://scitechdaily.com/feed/",
                ],
                "pexels_style": "realistic",
                "hashtags": "#Space #Astronomy #Universe #BlackHole #NASA #Cosmos #Astrophysics",
                "voice": "en-GB-RyanNeural",
            },
            "tech_ai": {
                "rss_feeds": [
                    "https://www.theverge.com/rss/index.xml",
                    "https://techcrunch.com/feed/",
                    "https://venturebeat.com/category/ai/feed/",
                    "https://www.artificialintelligence-news.com/feed/",
                    "https://www.wired.com/feed/tag/ai/latest/rss",
                ],
                "pexels_style": "futuristic",
                "hashtags": "#AI #ArtificialIntelligence #Cyberpunk #TechNews #FutureTech #Robotics",
                "voice": "en-US-GuyNeural",
            },
            # "finance_economy": {
            #     "rss_feeds": [
            #         "https://feeds.reuters.com/reuters/businessNews",
            #         "https://www.marketwatch.com/rss/topstories",
            #         "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
            #         "https://www.businessinsider.com/rss",
            #     ],
            #     "pexels_style": "business",
            #     "hashtags": "#Finance #Economy #MoneyFacts #StockMarket #Investment #FinanceFacts",
            #     "voice": "en-US-GuyNeural",
            # },
            # "bizarre_facts": {
            #     "rss_feeds": [
            #         "https://www.zmescience.com/feed/",
            #         "https://www.atlasobscura.com/feeds/latest",
            #         "https://www.mentalfloss.com/rss.xml",
            #         "https://www.livescience.com/feeds/all",
            #         "https://www.odditycentral.com/feed",
            #     ],
            #     "pexels_style": "nature",
            #     "hashtags": "#BizarreFacts #WeirdFacts #DidYouKnow #MindBlowing #StrangeFacts #Shocking",
            #     "voice": "en-US-ChristopherNeural",
            # },
            # "psychology": {
            #     "rss_feeds": [
            #         "https://www.sciencedaily.com/rss/mind_brain/psychology.xml",
            #         "https://www.psypost.org/feed/",
            #         "https://neurosciencenews.com/neuroscience-topics/psychology/feed/",
            #         "https://digest.bps.org.uk/feed/",
            #         "https://www.apa.org/news/psycport/psycport.rss",
            #     ],
            #     "pexels_style": "human",
            #     "hashtags": "#Psychology #BodyLanguage #DarkPsychology #MindTricks #Manipulation #MentalHealth",
            #     "voice": "en-US-BrianNeural",
            # },
            # "health_wellness": {
            #     "rss_feeds": [
            #         "https://www.sciencedaily.com/rss/health_medicine/",
            #         "https://www.medicalnewstoday.com/rss/medicalnewstoday.xml",
            #         "https://www.healthline.com/rss/",
            #         "https://feeds.webmd.com/rss/rss.aspx?RSSSource=RSS_PUBLIC",
            #         "https://www.who.int/rss-feeds/news-english.xml",
            #     ],
            #     "pexels_style": "medical",
            #     "hashtags": "#Health #Wellness #HealthFacts #MedicalFacts #BodyFacts #HealthTips",
            #     "voice": "en-US-JennyNeural",
            # },
            # "animals_nature": {
            #     "rss_feeds": [
            #         "https://www.sciencedaily.com/rss/plants_animals/",
            #         "https://feeds.nationalgeographic.com/ng/News/News_Main",
            #         "https://www.livescience.com/feeds/all",
            #         "https://insider.si.edu/category/animals/feed/",
            #         "https://www.earth.com/feed/",
            #     ],
            #     "pexels_style": "wildlife",
            #     "hashtags": "#Animals #Wildlife #Nature #WildAnimals #AnimalFacts #NatureFacts",
            #     "voice": "en-AU-WilliamNeural",
            # },
        }

    def get_time_slot(self):
        h = datetime.datetime.now().hour
        if 0 <= h < 4:
            return "mid_night"
        elif 4 <= h < 8:
            return "4_am"
        elif 8 <= h < 12:
            return "8_am"
        elif 12 <= h < 16:
            return "mid_day"
        elif 16 <= h < 20:
            return "4_pm"
        else:
            return "8_pm"

    def fetch_rss(self, url):
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                entries = feedparser.parse(r.content).entries[:10]
                if entries:
                    return entries
            else:
                print(f"      ⚠️ RSS returned {r.status_code}: {url}")
        except Exception as e:
            print(f"      ⚠️ RSS Error ({url}): {type(e).__name__}")
        return []

    def pick_top_3_viral_topics(self, candidates, niche):
        titles = [f"{i}. {c['title']}" for i, c in enumerate(candidates)]
        titles_text = "\n".join(titles)

        prompt = f"""
            TASK: Pick THREE headlines with the highest potential to go VIRAL as YouTube Shorts.
            NICHE: {niche}

            SELECTION RULES:
            1. Prefer FACTUAL, SURPRISING, or EDUCATIONAL topics.
            2. DO NOT pick: opinion pieces, personal interviews, travel diaries, or listicles without substance.

            HEADLINES:
            {titles_text}

            OUTPUT FORMAT: Return ONLY a JSON dict with key "picks" containing exactly 3 objects.
            Each object must have:
              - "index": integer (the headline number from the list above)
              - "hook": string (one punchy sentence, max 15 words, why this would go viral)
        """
        try:
            # Uses the newly upgraded AIEngine logic with Tenacity retries
            response_text = self.ai.generate(
                system_prompt="You output ONLY valid JSON dictionaries.",
                user_prompt=prompt,
                require_json=True,
            )
            response_data = json.loads(response_text.strip())
            picks = response_data.get("picks", [])

            results = []
            for pick in picks[:3]:
                idx = pick.get("index")
                if isinstance(idx, int) and 0 <= idx < len(candidates):
                    c = candidates[idx]
                    results.append(
                        {
                            "title": c["title"],
                            "summary": re.sub(r"<[^>]+>", "", c.get("summary", ""))[
                                :250
                            ],
                            "reason": pick.get("hook", "High viral potential"),
                            "link": c.get("link", ""),
                        }
                    )

            if results:
                return results

        except Exception as e:
            print(f"      ⚠️ AI topic picker error: {e}. Using random fallback.")

        return [
            {
                "title": c["title"],
                "summary": re.sub(r"<[^>]+>", "", c.get("summary", ""))[:250],
                "reason": "Selected from pool",
                "link": c.get("link", ""),
            }
            for c in random.sample(candidates, min(3, len(candidates)))
        ]

    # ─────────────────────────────────────────────
    # ROBUST ARTICLE EXTRACTOR
    # ─────────────────────────────────────────────
    def _normalize_article_line(self, line):
        line = html.unescape(str(line or "")).strip()
        line = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"https?://\S+", " ", line)
        line = re.sub(r"^\s{0,4}#{1,6}\s*", "", line)
        line = re.sub(r"^\s{0,4}[-*+]\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip(" -|")
        return line

    def _is_noise_line(self, raw_line, clean_line):
        if not clean_line:
            return True

        raw = str(raw_line or "").strip()
        lower = clean_line.lower()

        metadata_prefixes = (
            "title:",
            "url source:",
            "published time:",
            "markdown content:",
        )
        if lower.startswith(metadata_prefixes):
            return True

        garbage_patterns = (
            "skip to main content",
            "sections",
            "search sections",
            "subscribe now",
            "select language",
            "when autocomplete results are available",
            "who regional websites",
            "opens in new window",
            "featured:",
            "coverage:",
            "newsletters",
            "events calendar",
            "digital edition",
            "privacy policy",
            "terms of use",
            "cookie policy",
            "advertisement",
            "sponsored content",
            "related articles",
            "most read",
            "read more",
            "share this",
            "follow us",
            "contact us",
            "about us",
            "all rights reserved",
        )
        if any(pattern in lower for pattern in garbage_patterns):
            return True

        if raw.startswith(("*", "- [", "[![", "![", "[](")):
            return True

        markdown_link_count = raw.count("](")
        if markdown_link_count >= 2:
            return True

        words = re.findall(r"[A-Za-z][A-Za-z'-]+", clean_line)
        if len(words) < 8:
            return True

        # Menus often have many short title-case labels but no sentence punctuation.
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", clean_line))
        if len(clean_line) < 90 and not has_sentence_punctuation:
            return True

        if markdown_link_count and len(clean_line) < 140 and not has_sentence_punctuation:
            return True

        return False

    def _article_quality_score(self, text):
        words = re.findall(r"[A-Za-z][A-Za-z'-]+", text)
        sentences = re.findall(r"[.!?](?:\s|$)", text)
        if len(words) < 50 or len(sentences) < 2:
            return 0

        lower = text.lower()
        noise_hits = sum(
            lower.count(term)
            for term in (
                "subscribe",
                "sections",
                "newsletter",
                "privacy policy",
                "cookie",
                "opens in new window",
                "all topics",
            )
        )
        return len(words) + len(sentences) * 20 - noise_hits * 80

    def clean_article_text(self, raw_text, title=""):
        if not raw_text:
            return None

        lines = raw_text.splitlines()
        metadata_title = ""
        for line in lines[:20]:
            match = re.match(r"^\s*Title:\s*(.+?)\s*$", line)
            if match:
                metadata_title = self._normalize_article_line(match.group(1))
                break

        article_title = title or metadata_title
        title_key = re.sub(r"\W+", "", article_title.lower()) if article_title else ""

        body_start = 0
        for i, line in enumerate(lines):
            if line.strip().lower() == "markdown content:":
                body_start = i + 1
                break

        body_lines = lines[body_start:]

        # Many Jina pages contain the headline once near the top navigation and
        # again immediately before the article. Prefer the later headline.
        if title_key:
            heading_matches = []
            for i, line in enumerate(body_lines):
                clean = self._normalize_article_line(line)
                clean_key = re.sub(r"\W+", "", clean.lower())
                if clean_key == title_key:
                    heading_matches.append(i)
            if len(heading_matches) >= 2:
                body_lines = body_lines[heading_matches[-1] + 1 :]
            elif len(heading_matches) == 1:
                body_lines = body_lines[heading_matches[0] + 1 :]

        paragraphs = []
        previous_line_was_image = False
        for raw_line in body_lines:
            raw_text = str(raw_line or "").strip()
            if raw_text.startswith(("![", "[![")):
                previous_line_was_image = True
                continue

            clean_line = self._normalize_article_line(raw_line)

            if title_key and re.sub(r"\W+", "", clean_line.lower()) == title_key:
                previous_line_was_image = False
                continue

            if self._is_noise_line(raw_line, clean_line):
                if previous_line_was_image and not clean_line:
                    continue
                previous_line_was_image = False
                continue

            if previous_line_was_image and re.search(
                r"\((?:[^)]*(?:afp|getty|reuters|ap photo|associated press|photo|image|credit|via)[^)]*)\)",
                clean_line,
                re.IGNORECASE,
            ):
                previous_line_was_image = False
                continue

            paragraphs.append(clean_line)
            previous_line_was_image = False

            if len(" ".join(paragraphs)) >= 6000:
                break

        text = " ".join(paragraphs)
        text = self.garbage_regex.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if self._article_quality_score(text) <= 0:
            return None

        return text

    @retry(
        wait=wait_exponential(multiplier=2, min=2, max=10), stop=stop_after_attempt(3)
    )
    def _fetch_jina(self, url):
        """Internal helper to fetch from Jina with automatic retries."""
        reader_url = f"https://r.jina.ai/{url}"
        headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
        res = requests.get(reader_url, headers=headers, timeout=20)

        if res.status_code == 200:
            if (
                "verify you are human" in res.text.lower()
                or "captcha" in res.text.lower()
            ):
                raise ValueError("CAPTCHA Blocked")
            return res.text
        raise ValueError(f"HTTP {res.status_code}")

    def extract_full_article(self, url):
        print(f"      📖 Deep Reading full article from: {url}")

        # 1. Primary Attempt: Jina AI
        try:
            full_text = self._fetch_jina(url)
            clean_text = self.clean_article_text(full_text)
            if clean_text:
                return clean_text[:5000]
        except Exception as e:
            print(
                f"      ⚠️ Jina Reader Failed ({e}). Falling back to Native Scrape..."
            )

        # 2. Free CPU Fallback: BeautifulSoup text extraction
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                soup = BeautifulSoup(res.content, "html.parser")
                for tag in soup(
                    [
                        "script",
                        "style",
                        "nav",
                        "header",
                        "footer",
                        "aside",
                        "form",
                        "button",
                    ]
                ):
                    tag.decompose()
                # Extract text strictly from paragraph tags to avoid nav/footer garbage
                paragraphs = soup.find_all("p")
                fallback_text = " ".join([p.get_text() for p in paragraphs])
                fallback_text = self.clean_article_text(fallback_text)

                if fallback_text:
                    print("      ✅ Native Scrape Successful.")
                    return fallback_text[:5000]
        except Exception as bs_e:
            print(f"      ❌ Native Scrape Failed: {bs_e}")

        return None

    def refine_user_idea(self, topic, content, feedback=""):
        print(f"      🧠 Refining idea: '{topic}'...")
        feedback_section = (
            f"\n**PREVIOUS FEEDBACK TO ADDRESS: {feedback}"
            if feedback
            else "Generate more appropriate keywords"
        )

        prompt = f"""
            TASK: Rewrite and expand this user idea into a clean, factual, well-structured article (300-500 words).
            TOPIC: {topic}
            USER'S IDEA: {content}
            {feedback_section}

            RULES:
            1. Only include facts — no opinions or fluff.
            2. Structure: background → key facts → significance.
            3. Do NOT add a title or headline.
            OUTPUT: Plain text article only.
        """
        try:
            return self.ai.generate(
                system_prompt="You are a factual research writer. Output plain text only.",
                user_prompt=prompt,
                require_json=False,
            ).strip()
        except Exception as e:
            print(f"      ❌ Idea refinement failed: {e}")
            return content

    def _pick_niche(self):
        used_niches = self.db.get_used_niches_today()
        all_niches = set(self.MASTER_NICHES.keys())
        available = list(all_niches - used_niches)
        if not available:
            print("⚠️ All niches used today. Resetting pool.")
            available = list(all_niches)
        return random.choice(available)

    def fetch_and_present_topics(self, slot):
        selected_niche = self._pick_niche()
        niche_data = self.MASTER_NICHES[selected_niche]
        sources = niche_data["rss_feeds"]

        print(
            f"\n🎯 Selected niche: '{selected_niche.upper()}' for slot: {slot.upper()}"
        )
        print(f"      ⚡ Fetching {len(sources)} RSS feeds in parallel...")

        candidates = []

        # Fetch RSS feeds simultaneously
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(sources)
        ) as executor:
            future_to_url = {
                executor.submit(self.fetch_rss, url): url for url in sources
            }
            for future in concurrent.futures.as_completed(future_to_url):
                entries = future.result()
                for e in entries:
                    if hasattr(e, "title"):
                        candidates.append(
                            {
                                "title": e.title,
                                "summary": getattr(e, "summary", e.title)[:3000],
                                "link": getattr(e, "link", ""),
                                "niche": selected_niche,
                            }
                        )

        if not candidates:
            print(f"❌ No RSS candidates found for '{selected_niche}'.")
            return None

        unique_candidates = [
            c for c in candidates if not self.db.task_exists(c["title"], c["link"])
        ]
        if len(unique_candidates) < 3:
            unique_candidates = candidates

        top_3 = self.pick_top_3_viral_topics(unique_candidates, selected_niche)
        if not top_3:
            return None

        return {
            "niche": selected_niche,
            "niche_data": niche_data,
            "slot": slot,
            "topics": top_3,
        }

    def save_approved_topic(self, chosen_topic, niche, niche_data, slot):
        full_content = self.extract_full_article(chosen_topic["link"])
        if not full_content:
            print("      ⚠️ Deep Read failed, falling back to RSS summary.")
            raw_summary = chosen_topic.get("summary", "")[:5000]
            clean_summary = re.sub(r"<[^>]+>", " ", raw_summary)

            # Use the pre-compiled regex to strip garbage instantly
            full_content = self.garbage_regex.sub("", clean_summary).strip()

        self.db.add_task(
            title=chosen_topic["title"],
            content=full_content,
            source=f"{niche.upper()}",
            status="pending",
            extra_data={
                "niche": niche,
                "niche_slot": slot,
                "source_url": chosen_topic["link"],
                "hashtags": niche_data.get("hashtags", "#Shorts #Viral"),
                "pexels_style": niche_data.get("pexels_style", "realistic"),
                "voice": niche_data.get("voice", "en-US-GuyNeural"),
                "target_language": "English",
            },
        )
        return self.db.collection.find_one(
            {"title": chosen_topic["title"], "status": "pending"}
        )

    def scrape_targeted_niche(self, forced_slot=None):
        slot = forced_slot if forced_slot else self.get_time_slot()
        result = self.fetch_and_present_topics(slot)
        if not result:
            return

        chosen = result["topics"][0]
        print(f"      🎉 Auto-selected topic: '{chosen['title'][:60]}'")
        self.save_approved_topic(chosen, result["niche"], result["niche_data"], slot)
