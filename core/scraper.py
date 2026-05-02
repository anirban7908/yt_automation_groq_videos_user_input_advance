import requests
import feedparser
import random
import datetime
import re
import json
import os
from groq import Groq
from core.db_manager import DBManager
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from core.ai_core import AIEngine

load_dotenv()


class NewsScraper:
    def __init__(self, **kwargs):
        self.db = DBManager()
        self.ai = AIEngine()

        # ════════════════════════════════════════════════════════
        # 8 TRENDING NICHES — English only
        # Trimmed to highest-performing niches across
        # YouTube Shorts, Instagram Reels, Facebook Reels.
        # hi_voice removed — pipeline is now English-only.
        # ════════════════════════════════════════════════════════
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
            # "space": {
            #     "rss_feeds": [
            #         "https://www.space.com/feeds/all",
            #         "https://universetoday.com/feed",
            #         "https://phys.org/rss-feed/space-news/",
            #         "https://www.nasa.gov/feeds/iotd-feed/",
            #         "https://spacenews.com/feed/",
            #         "https://scitechdaily.com/feed/",
            #     ],
            #     "pexels_style": "realistic",
            #     "hashtags": "#Space #Astronomy #Universe #BlackHole #NASA #Cosmos #Astrophysics",
            #     "voice": "en-GB-RyanNeural",
            # },
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
            "finance_economy": {
                "rss_feeds": [
                    "https://feeds.reuters.com/reuters/businessNews",
                    "https://www.marketwatch.com/rss/topstories",
                    "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
                    "https://www.businessinsider.com/rss",
                ],
                "pexels_style": "business",
                "hashtags": "#Finance #Economy #MoneyFacts #StockMarket #Investment #FinanceFacts",
                "voice": "en-US-GuyNeural",
            },
            "bizarre_facts": {
                "rss_feeds": [
                    "https://www.zmescience.com/feed/",
                    "https://www.atlasobscura.com/feeds/latest",
                    "https://www.mentalfloss.com/rss.xml",
                    "https://www.livescience.com/feeds/all",
                    "https://www.odditycentral.com/feed",
                ],
                "pexels_style": "nature",
                "hashtags": "#BizarreFacts #WeirdFacts #DidYouKnow #MindBlowing #StrangeFacts #Shocking",
                "voice": "en-US-ChristopherNeural",
            },
        }

    # ─────────────────────────────────────────────
    # TIME SLOT LOGIC
    # ─────────────────────────────────────────────

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

    # ─────────────────────────────────────────────
    # RSS FETCHER
    # ─────────────────────────────────────────────

    def fetch_rss(self, url):
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            print(f"      🔗 Fetching: {url}")
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                entries = feedparser.parse(r.content).entries[:10]
                if entries:
                    print(f"         ✅ Found {len(entries)} articles.")
                    return entries
        except:
            pass
        return []

    # ─────────────────────────────────────────────
    # AI TOPIC PICKER — enriched output with hook + summary
    # ─────────────────────────────────────────────

    def pick_top_3_viral_topics(self, candidates, niche):
        """
        Uses Groq to pick the 3 most viral-worthy headlines from RSS candidates.
        Returns enriched list of dicts: {title, summary, reason, link}
        so main.py can display them clearly to the user for approval.
        """
        titles = [f"{i}. {c['title']}" for i, c in enumerate(candidates)]
        titles_text = "\n".join(titles)

        prompt = f"""
            TASK: Pick THREE headlines with the highest potential to go VIRAL as YouTube Shorts.
            NICHE: {niche}

            SELECTION RULES:
            1. Prefer FACTUAL, SURPRISING, or EDUCATIONAL topics — scientific discoveries,
            historical revelations, weird facts, health breakthroughs, financial insights,
            animal behavior, or major world events.
            2. DO NOT pick: opinion pieces, personal interviews, travel diaries,
            product reviews, motivational stories, or listicles without substance.
            3. For 'bizarre_facts': prioritize the most surprising/shocking facts.
            4. For 'finance_economy': prioritize stories with real numbers and consequences.
            5. For 'animals_nature': prioritize unusual animal behavior or new species discoveries.
            6. For 'history_world': prioritize newly discovered or little-known historical facts.

            HEADLINES:
            {titles_text}

            OUTPUT FORMAT: Return ONLY a JSON dict with key "picks" containing exactly 3 objects.
            Each object must have:
              - "index": integer (the headline number from the list above)
              - "hook": string (one punchy sentence, max 15 words, why this would go viral)

            Example: {{"picks": [{{"index": 5, "hook": "Nobody knows this WW2 secret even existed"}}, {{"index": 2, "hook": "This spider can survive a nuclear blast"}}, {{"index": 9, "hook": "The real reason Rome fell in one night"}}]}}
        """
        try:
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

        # Fallback — random sample with placeholder reason
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
    # ARTICLE EXTRACTOR
    # ─────────────────────────────────────────────

    def extract_full_article(self, url):
        """Visits the webpage and extracts paragraph text bypassing anti-bot walls."""
        print(f"      📖 Deep Reading full article from: {url}")
        try:
            reader_url = f"https://r.jina.ai/{url}"
            headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
            res = requests.get(reader_url, headers=headers, timeout=20)

            if res.status_code == 200:
                full_text = res.text
                if (
                    "verify you are human" in full_text.lower()
                    or "captcha" in full_text.lower()
                ):
                    print("      ⚠️ Reader API was blocked by a CAPTCHA.")
                    return None
                if len(full_text) > 300:
                    return full_text[:5000]
            return None
        except Exception as e:
            print(f"      ❌ Failed to read full article: {e}")
            return None

    # ─────────────────────────────────────────────
    # MANUAL IDEA REFINER (used by main.py)
    # ─────────────────────────────────────────────

    def refine_user_idea(self, topic, content, feedback=""):
        """
        Takes a user's raw topic/content and uses Groq to refine it
        into a clean, factual article-style source text for brain.py.
        Called by main.py in manual mode.
        """
        print(f"      🧠 Refining idea: '{topic}'...")

        feedback_section = (
            f"\n**PREVIOUS FEEDBACK TO ADDRESS: {feedback}"
            if feedback
            else "Generate more appropriate keywords"
        )

        prompt = f"""
            TASK: You are a research assistant. A user wants to make a YouTube Short about the following topic.
            Rewrite and expand their idea into a clean, factual, well-structured article (300-500 words)
            that a scriptwriter can use as source material.

            TOPIC: {topic}
            USER'S IDEA: {content}
            {feedback_section}

            RULES:
            1. Only include facts — no opinions, no fluff, no personal stories.
            2. Structure it clearly: background → key facts → significance.
            3. Use simple language. Avoid jargon.
            4. Do NOT add a title or headline — just the body text.
            5. If the idea is too vague, make reasonable factual assumptions and expand on them.

            OUTPUT: Plain text article only. No JSON, no bullet points, no headers.
        """
        try:
            response_text = self.ai.generate(
                system_prompt="You are a factual research writer. Output plain text only.",
                user_prompt=prompt,
                require_json=False,
            )
            return response_text.strip()
        except Exception as e:
            print(f"      ❌ Idea refinement failed: {e}")
            return content  # Fall back to original if AI fails

    # ─────────────────────────────────────────────
    # NICHE PICKER HELPER
    # ─────────────────────────────────────────────

    def _pick_niche(self):
        """Pick an unused niche for today, falling back to full pool if all used."""
        used_niches = self.db.get_used_niches_today()
        all_niches = set(self.MASTER_NICHES.keys())
        available = list(all_niches - used_niches)
        if not available:
            print("⚠️ All niches used today. Resetting pool.")
            available = list(all_niches)
        return random.choice(available)

    # ─────────────────────────────────────────────
    # INTERACTIVE TOPIC FETCHER — called by main.py
    # ─────────────────────────────────────────────

    def fetch_and_present_topics(self, slot):
        """
        Fetches RSS articles for a randomly chosen niche, runs the AI topic picker,
        and returns a result dict WITHOUT writing to DB. Used by main.py interactive loop.

        Returns:
        {
            "niche": str,
            "niche_data": dict,
            "slot": str,
            "topics": [
                {"title": str, "summary": str, "reason": str, "link": str},
                ...  (3 items)
            ]
        }
        Returns None if no candidates found.
        """
        selected_niche = self._pick_niche()
        niche_data = self.MASTER_NICHES[selected_niche]
        sources = niche_data["rss_feeds"]

        print(
            f"\n🎯 Selected niche: '{selected_niche.upper()}' for slot: {slot.upper()}"
        )

        candidates = []
        for url in sources:
            for e in self.fetch_rss(url):
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

        # Pre-filter duplicates so user only sees fresh topics
        unique_candidates = [
            c for c in candidates if not self.db.task_exists(c["title"], c["link"])
        ]
        # Fall back to full pool if fewer than 3 unique candidates
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

            # Strip common RSS garbage that pollutes the AI's logic
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
            for phrase in garbage_phrases:
                clean_summary = re.compile(re.escape(phrase), re.IGNORECASE).sub(
                    "", clean_summary
                )

            full_content = clean_summary.strip()

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

    # ─────────────────────────────────────────────
    # MAIN SCRAPER ORCHESTRATOR
    # ─────────────────────────────────────────────

    def scrape_targeted_niche(self, forced_slot=None):
        """
        Legacy automated mode — silently picks a topic and saves to DB.
        Used when running without interactive approval (e.g. scheduled/cron runs).
        """
        slot = forced_slot if forced_slot else self.get_time_slot()
        result = self.fetch_and_present_topics(slot)
        if not result:
            return

        # Auto-select the first (highest-ranked) topic in automated mode
        chosen = result["topics"][0]
        print(f"      🎉 Auto-selected topic: '{chosen['title'][:60]}'")
        self.save_approved_topic(chosen, result["niche"], result["niche_data"], slot)
