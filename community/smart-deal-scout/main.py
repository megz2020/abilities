import asyncio
import json
import re
import httpx
from datetime import datetime, timezone
from time import time

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

# =============================================================================
# SMART DEAL SCOUT — Interactive Skill
# Voice-first price tracking and deal finding.
# Pairs with background.py which proactively alerts on price drops.
#
# SETUP: Set SERPER_API_KEY below (same value in background.py), then upload.
# Get a free key at serper.dev (2,500 searches/month, no credit card).
# =============================================================================

# --- Configuration ---
SERPER_API_KEY = ""
SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
SERPER_SEARCH_URL = "https://google.serper.dev/search"
WATCHLIST_FILE = "deal_scout_watchlist.json"
MAX_TRACKED_ITEMS = 15
MAX_SEARCH_RESULTS = 5
DROP_THRESHOLD_PCT = 10
CHECK_INTERVAL_HOURS = 6
MAX_TURNS = 20
IDLE_REPROMPT = 2
IDLE_EXIT = 3

EXIT_WORDS = {
    "stop", "exit", "quit", "done", "bye", "goodbye", "cancel",
    "no thanks", "no thank you", "that's all", "that's it",
    "all done", "i'm done", "im done", "thank you", "thanks",
    "cheers", "great thanks", "ok thanks", "okay thanks",
}

TRACK_KW = {"track", "watch", "monitor", "alert me", "notify me", "watching"}
SEARCH_KW = {"find", "search", "cheapest", "best deal", "deals on", "price of",
             "how much", "shop for", "looking for", "compare"}
CHECK_KW  = {"check", "any deals", "price drops", "updates", "deal update",
             "what's changed", "whats changed", "price check", "any changes"}
LIST_KW   = {"list", "watchlist", "what am i tracking", "show my", "what's tracked",
             "whats tracked", "my deals", "my list"}
REMOVE_KW = {"remove", "stop tracking", "delete", "untrack", "drop"}
DETAIL_KW = ("more", "detail", "tell me about", "the first", "the second",
             "the third", "that one", "first one", "second one", "third one",
             "ingredients", "specs", "about it", "about that")

_ORDINAL = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}


# =============================================================================
# Module-level helpers
# =============================================================================

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_price(text: str) -> float:
    if not text:
        return 0.0
    cleaned = re.sub(r"[,$€£¥]", "", str(text))
    m = re.search(r"\d[\d,.]*", cleaned)
    if m:
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            pass
    return 0.0


def _make_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")[:30]
    return "%s-%d" % (slug, int(time()))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(iso_ts: str) -> float:
    if not iso_ts:
        return 9999.0
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600
    except Exception:
        return 9999.0


# =============================================================================
# Main Capability
# =============================================================================

class SmartDealScoutCapability(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None

    _last_results: list = None
    _trigger_text: str = ""

    # {{register capability}}

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------

    def _log(self, msg: str):
        try:
            self.worker.editor_logging_handler.info("[DealScout] %s" % msg)
        except Exception:
            pass

    def _err(self, msg: str):
        try:
            self.worker.editor_logging_handler.error("[DealScout] %s" % msg)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Config
    # -------------------------------------------------------------------------

    def _config_ok(self) -> bool:
        return bool(SERPER_API_KEY.strip())

    # -------------------------------------------------------------------------
    # File storage helpers
    # -------------------------------------------------------------------------

    async def _load_watchlist(self) -> dict:
        empty = {"items": [], "settings": {"drop_threshold_pct": DROP_THRESHOLD_PCT,
                                            "max_items": MAX_TRACKED_ITEMS}}
        try:
            exists = await self.capability_worker.check_if_file_exists(WATCHLIST_FILE, False)
            if not exists:
                return empty
            raw = await self.capability_worker.read_file(WATCHLIST_FILE, False)
            if not (raw or "").strip():
                return empty
            data = json.loads(raw)
            if isinstance(data, dict) and "items" in data:
                return data
        except Exception as exc:
            self._err("load watchlist: %s" % exc)
        return empty

    async def _save_watchlist(self, data: dict):
        try:
            exists = await self.capability_worker.check_if_file_exists(WATCHLIST_FILE, False)
            if exists:
                await self.capability_worker.delete_file(WATCHLIST_FILE, False)
            await self.capability_worker.write_file(
                WATCHLIST_FILE, json.dumps(data, ensure_ascii=False), False
            )
        except Exception as exc:
            self._err("save watchlist: %s" % exc)

    # -------------------------------------------------------------------------
    # Serper API
    # -------------------------------------------------------------------------

    async def _search_shopping(self, query: str, num: int = MAX_SEARCH_RESULTS) -> list:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    SERPER_SHOPPING_URL,
                    headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                    json={"q": query, "num": num},
                )
                resp.raise_for_status()
                return resp.json().get("shopping", [])
        except Exception as exc:
            self._err("shopping search: %s" % exc)
            return []

    async def _search_web(self, query: str, num: int = MAX_SEARCH_RESULTS) -> list:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    SERPER_SEARCH_URL,
                    headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                    json={"q": query, "num": num},
                )
                resp.raise_for_status()
                return resp.json().get("organic", [])
        except Exception as exc:
            self._err("web search: %s" % exc)
            return []

    async def _search_product(self, query: str) -> list:
        shopping = await self._search_shopping("%s buy price" % query)
        if shopping:
            results = []
            for item in shopping[:MAX_SEARCH_RESULTS]:
                price = _parse_price(item.get("price", "0"))
                if price > 0:
                    results.append({
                        "title": item.get("title", ""),
                        "price": price,
                        "price_str": item.get("price", "N/A"),
                        "source": item.get("source", ""),
                        "rating": item.get("rating"),
                        "delivery": item.get("delivery", ""),
                    })
            if results:
                return results

        web = await self._search_web("%s price buy" % query)
        if not web:
            return []
        snippets = "".join(
            "- %s: %s\n" % (r.get("title", ""), r.get("snippet", ""))
            for r in web[:4]
        )
        raw = await asyncio.to_thread(
            self.capability_worker.text_to_text_response,
            "Extract up to 3 product prices from these search results.\n"
            'Return ONLY a JSON array: [{"title": str, "price": float, "source": str}]\n'
            "Results:\n%s" % snippets
        )
        try:
            items = json.loads(_strip_fences(raw))
            return [
                {
                    "title": i.get("title", ""),
                    "price": float(i.get("price", 0)),
                    "price_str": "$%.2f" % float(i.get("price", 0)),
                    "source": i.get("source", ""),
                    "rating": None,
                    "delivery": "",
                }
                for i in items if float(i.get("price", 0)) > 0
            ]
        except Exception:
            return []

    # -------------------------------------------------------------------------
    # Price utilities
    # -------------------------------------------------------------------------

    def _best_price(self, results: list) -> tuple:
        if not results:
            return 0.0, "", ""
        cheapest = min(results, key=lambda r: r.get("price", 999999))
        return cheapest.get("price", 0.0), cheapest.get("source", ""), cheapest.get("title", "")

    def _calc_drop(self, baseline: float, current: float) -> tuple:
        if baseline <= 0:
            return 0.0, 0.0, False
        drop_amt = baseline - current
        drop_pct = (drop_amt / baseline) * 100.0
        return round(drop_amt, 2), round(drop_pct, 1), drop_pct >= DROP_THRESHOLD_PCT

    # -------------------------------------------------------------------------
    # Intent classification
    # -------------------------------------------------------------------------

    def _wants_exit(self, text: str) -> bool:
        lo = text.lower().strip()
        if any(w in lo for w in EXIT_WORDS):
            return True
        if len(lo.split()) <= 4:
            ans = self.capability_worker.text_to_text_response(
                'Does this mean the user wants to stop or say goodbye?\nInput: "%s"\nReply YES or NO only.' % text
            ).strip().upper()
            return ans.startswith("YES")
        return False

    def _classify_intent(self, text: str) -> dict:
        lo = text.lower()
        if any(k in lo for k in TRACK_KW):
            return {"intent": "track", "product": None, "budget": None}
        if any(k in lo for k in SEARCH_KW):
            return {"intent": "search", "product": None, "budget": None}
        if any(k in lo for k in CHECK_KW):
            return {"intent": "check", "product": None, "budget": None}
        if any(k in lo for k in LIST_KW):
            return {"intent": "list", "product": None, "budget": None}
        if any(k in lo for k in REMOVE_KW):
            return {"intent": "remove", "product": None, "budget": None}
        if self._last_results and any(t in lo for t in DETAIL_KW):
            return {"intent": "detail", "product": None, "budget": None}

        raw = self.capability_worker.text_to_text_response(
            "Classify this voice input into one of: track, search, check, list, remove, detail, exit, clarify.\n"
            "- track: user wants to watch/track a product price\n"
            "- search: user wants to find deals or compare prices right now\n"
            "- check: user wants price updates on their tracked items\n"
            "- list: user wants to see their tracked items\n"
            "- remove: user wants to stop tracking something\n"
            "- detail: user wants more info about a previously shown result\n"
            "- exit: user wants to leave\n"
            "- clarify: unclear\n"
            'Return ONLY JSON: {"intent": "...", "product": "..." or null, "budget": number or null}\n'
            'Input: "%s"' % text
        )
        try:
            result = json.loads(_strip_fences(raw))
            valid = ("track", "search", "check", "list", "remove", "detail", "exit", "clarify")
            if result.get("intent") in valid:
                return result
        except Exception:
            pass
        # Single-word retry
        raw2 = self.capability_worker.text_to_text_response(
            'Reply with ONLY one word: track, search, check, list, remove, detail, exit, or clarify.\nInput: "%s"' % text
        ).strip().lower()
        for v in ("track", "search", "check", "list", "remove", "detail", "exit"):
            if v in raw2:
                return {"intent": v, "product": None, "budget": None}
        return {"intent": "clarify", "product": None, "budget": None}

    def _extract_product_budget(self, text: str, hint: str = None) -> tuple:
        raw = self.capability_worker.text_to_text_response(
            "Extract the product name and optional budget from this voice input.\n"
            'Return ONLY JSON: {"product": "name here", "budget": number or null}\n'
            "Budget: 'under $80' -> 80, 'less than 150' -> 150, no budget -> null\n"
            'Input: "%s"' % text
        )
        try:
            data = json.loads(_strip_fences(raw))
            product = (data.get("product") or hint or "").strip()
            budget = data.get("budget")
            return product, float(budget) if budget else None
        except Exception:
            return (hint or text).strip(), None

    def _guess_product(self, text: str) -> str:
        raw = self.capability_worker.text_to_text_response(
            "This voice input may be garbled by speech recognition.\n"
            "If the user seems to be naming a product to track or search for, "
            "reply with only the most likely product name (2–6 words).\n"
            "If you cannot detect a product, reply: NONE\n"
            'Input: "%s"' % text
        ).strip()
        if not raw or raw.upper() == "NONE" or len(raw) > 60:
            return ""
        return raw

    def _match_item(self, product: str, items: list) -> int:
        if not items or not product:
            return -1
        lo = product.lower()
        for i, item in enumerate(items):
            if lo in item.get("name", "").lower() or item.get("name", "").lower() in lo:
                return i
        names = "\n".join("%d. %s" % (i + 1, item.get("name", "")) for i, item in enumerate(items))
        raw = self.capability_worker.text_to_text_response(
            'Which item best matches "%s"? Reply only with the number (1-%d) or 0 if none.\n%s'
            % (product, len(items), names)
        ).strip()
        m = re.search(r"\d+", raw)
        if m:
            idx = int(m.group()) - 1
            if 0 <= idx < len(items):
                return idx
        return -1

    # -------------------------------------------------------------------------
    # Intent handlers
    # -------------------------------------------------------------------------

    async def _handle_search(self, user_input: str):
        await self.capability_worker.speak("Let me search for that...")
        results = await self._search_product(user_input)
        self._last_results = results

        if not results:
            await self.capability_worker.speak(
                "I couldn't find pricing for that. Try a more specific product name."
            )
            return

        lines = ""
        for i, r in enumerate(results[:3], 1):
            rating = (", rated %s stars" % r["rating"]) if r.get("rating") else ""
            lines += "%d. %s — %s at %s%s\n" % (i, r["title"], r["price_str"], r["source"], rating)

        summary = await asyncio.to_thread(
            self.capability_worker.text_to_text_response,
            'The user asked: "%s"\n\nTop results:\n%s\n'
            "Give a friendly 3-4 sentence voice response listing these deals. "
            "Mention product name, price, and store. "
            "End by asking if they want details or to track any item. "
            "No markdown. No URLs." % (user_input, lines)
        )
        await self.capability_worker.speak(summary)

    async def _handle_track(self, user_input: str, product_hint: str = None):
        product, budget = await asyncio.to_thread(
            self._extract_product_budget, user_input, product_hint
        )

        if not product:
            product = await self.capability_worker.run_io_loop(
                "What product would you like to track? Please say the product name."
            )
            if not product or not product.strip():
                await self.capability_worker.speak("No product name given. Going back.")
                return

        wl = await self._load_watchlist()
        items = wl.get("items", [])
        cap = wl.get("settings", {}).get("max_items", MAX_TRACKED_ITEMS)

        dup = self._match_item(product, items)
        if dup >= 0:
            existing = items[dup]
            resp = await self.capability_worker.run_io_loop(
                "You're already tracking %s at a $%d baseline. "
                "Want to update your target price? Say yes or give me a new target."
                % (existing["name"], int(existing.get("baseline_price", 0)))
            )
            lo = (resp or "").lower()
            if any(w in lo for w in ("yes", "yep", "yeah", "sure", "ok", "okay")):
                raw_target = await self.capability_worker.run_io_loop(
                    "What's your target price? For example, 'under 150 dollars'."
                )
                new_budget = _parse_price(raw_target) if raw_target else 0.0
                if new_budget > 0:
                    items[dup]["budget"] = new_budget
                    await self._save_watchlist(wl)
                    await self.capability_worker.speak(
                        "Updated! I'll alert you when %s drops below $%d."
                        % (existing["name"], int(new_budget))
                    )
                else:
                    await self.capability_worker.speak("No changes made.")
            return

        if len(items) >= cap:
            await self.capability_worker.speak(
                "Your list is full at %d items. Say 'remove' followed by a product name to free up space." % cap
            )
            return

        await self.capability_worker.speak("Looking up the current price for %s..." % product)
        results = await self._search_product(product)

        if not results:
            await self.capability_worker.speak(
                "I couldn't find a price for %s. Try a more specific name." % product
            )
            return

        price, source, title = self._best_price(results)
        self._last_results = results

        if budget is None:
            resp = await self.capability_worker.run_io_loop(
                "Found %s at $%d on %s. I'll use that as your baseline. "
                "Do you have a target price? Say something like 'under 150 dollars', or say 'no'."
                % (title[:40], int(price), source)
            )
            if resp and not any(w in resp.lower() for w in ("no", "nope", "skip", "none")):
                parsed = _parse_price(resp)
                if parsed > 0:
                    budget = parsed
        else:
            await self.capability_worker.speak(
                "Found %s at $%d on %s." % (title[:40], int(price), source)
            )

        item = {
            "id": _make_id(product),
            "name": product,
            "search_query": "%s buy price" % product,
            "budget": budget,
            "baseline_price": price,
            "lowest_seen": price,
            "last_checked": _now_iso(),
            "last_price": price,
            "source": source,
            "added_at": _now_iso(),
        }
        items.append(item)
        wl["items"] = items
        await self._save_watchlist(wl)

        budget_msg = " with a target of $%d" % int(budget) if budget else ""
        await self.capability_worker.speak(
            "Done! Tracking %s%s. The background scout will alert you on any price drop." % (product, budget_msg)
        )

    async def _handle_check(self):
        wl = await self._load_watchlist()
        items = wl.get("items", [])

        if not items:
            await self.capability_worker.speak(
                "You're not tracking anything yet. Say 'track' followed by a product name to start."
            )
            return

        count = len(items)
        await self.capability_worker.speak(
            "Checking your %d tracked item%s..." % (count, "s" if count != 1 else "")
        )

        drops, unchanged, increases = [], [], []

        for item in items:
            results = await self._search_product(item["search_query"])
            if not results:
                unchanged.append("%s — couldn't reach price data" % item["name"])
                continue

            price, source, _ = self._best_price(results)
            if price <= 0:
                unchanged.append("%s — price unavailable" % item["name"])
                continue

            baseline = item.get("baseline_price", 0)
            drop_amt, drop_pct, is_significant = self._calc_drop(baseline, price)

            item["last_price"] = price
            item["last_checked"] = _now_iso()
            item["source"] = source
            if price < item.get("lowest_seen", baseline):
                item["lowest_seen"] = price

            tgt = item.get("budget")
            if drop_pct > 0:
                hit = " — HIT YOUR TARGET!" if (tgt and price <= tgt) else ""
                drops.append((
                    drop_pct,
                    "%s — down $%d to $%d (%.0f%% off)%s on %s"
                    % (item["name"], int(drop_amt), int(price), drop_pct, hit, source)
                ))
            elif price > baseline:
                increases.append(
                    "%s — up $%d to $%d on %s" % (item["name"], int(price - baseline), int(price), source)
                )
            else:
                unchanged.append(
                    "%s — still $%d at %s" % (item["name"], int(price), source)
                )

        await self._save_watchlist(wl)

        drops.sort(key=lambda x: x[0], reverse=True)
        drop_lines = [d[1] for d in drops]

        if not drop_lines and not increases:
            await self.capability_worker.speak(
                "No price changes on your tracked items. Everything is holding steady."
            )
            return

        summary_data = json.dumps({"drops": drop_lines, "unchanged": unchanged, "increases": increases})
        summary = await asyncio.to_thread(
            self.capability_worker.text_to_text_response,
            "Give a concise voice update about these price changes. "
            "Lead with good news (drops) first. Be enthusiastic about target hits. "
            "Keep it to 3-5 sentences. No markdown.\nData: %s" % summary_data
        )
        await self.capability_worker.speak(summary)

    async def _handle_list(self):
        wl = await self._load_watchlist()
        items = wl.get("items", [])

        if not items:
            await self.capability_worker.speak(
                "Your list is empty. Say 'track' followed by a product to start."
            )
            return

        lines = ""
        for i, item in enumerate(items, 1):
            last = item.get("last_price", item.get("baseline_price", 0))
            tgt = ", target $%d" % int(item["budget"]) if item.get("budget") else ""
            lines += "%d. %s — baseline $%d, now $%d%s\n" % (
                i, item["name"], int(item.get("baseline_price", 0)), int(last), tgt
            )

        summary = await asyncio.to_thread(
            self.capability_worker.text_to_text_response,
            "Read out this price tracking list in a friendly voice summary. Keep it brief.\n"
            "Items:\n%s\nEnd by asking if they want to remove any item or check for deals. No markdown." % lines
        )
        await self.capability_worker.speak(summary)

    async def _handle_remove(self, user_input: str):
        wl = await self._load_watchlist()
        items = wl.get("items", [])

        if not items:
            await self.capability_worker.speak("Your list is already empty.")
            return

        idx = self._match_item(user_input, items)
        if idx < 0:
            await self.capability_worker.speak(
                "I couldn't find that in your list. Say 'list' to hear what you're tracking."
            )
            return

        removed = items.pop(idx)
        wl["items"] = items
        await self._save_watchlist(wl)

        remaining = len(items)
        await self.capability_worker.speak(
            "Removed %s. You're now tracking %d item%s."
            % (removed["name"], remaining, "s" if remaining != 1 else "")
        )

    async def _handle_detail(self, user_input: str):
        if not self._last_results:
            await self.capability_worker.speak(
                "I don't have recent search results. Try searching for a product first."
            )
            return

        lo = user_input.lower()
        idx = 0
        for word, i in _ORDINAL.items():
            if word in lo and i < len(self._last_results):
                idx = i
                break

        r = self._last_results[idx]
        detail = await asyncio.to_thread(
            self.capability_worker.text_to_text_response,
            "Give a brief 2-3 sentence voice summary of this product.\n"
            "Product: %s\nPrice: %s\nStore: %s\nRating: %s\nDelivery: %s\n"
            "End by asking if they want to track it. No markdown."
            % (r.get("title", ""), r.get("price_str", ""), r.get("source", ""),
               r.get("rating", "N/A"), r.get("delivery", ""))
        )
        await self.capability_worker.speak(detail)

    # -------------------------------------------------------------------------
    # Track-from-search shortcut
    # -------------------------------------------------------------------------

    async def _maybe_track_from_last(self, user_input: str) -> bool:
        if not self._last_results:
            return False
        lo = user_input.lower()
        if not any(w in lo for w in ("track", "watch", "track that", "track it", "add that")):
            return False
        idx = 0
        for word, i in _ORDINAL.items():
            if word in lo and i < len(self._last_results):
                idx = i
                break
        r = self._last_results[idx]
        name = r.get("title", "")
        price = r.get("price", 0.0)
        source = r.get("source", "")

        wl = await self._load_watchlist()
        items = wl.get("items", [])
        cap = wl.get("settings", {}).get("max_items", MAX_TRACKED_ITEMS)

        if self._match_item(name, items) >= 0:
            await self.capability_worker.speak(
                "You're already tracking something similar. Say 'list' to see your items."
            )
            return True

        if len(items) >= cap:
            await self.capability_worker.speak(
                "Your list is full. Remove an item first."
            )
            return True

        item = {
            "id": _make_id(name),
            "name": name,
            "search_query": "%s buy price" % name,
            "budget": None,
            "baseline_price": price,
            "lowest_seen": price,
            "last_checked": _now_iso(),
            "last_price": price,
            "source": source,
            "added_at": _now_iso(),
        }
        items.append(item)
        wl["items"] = items
        await self._save_watchlist(wl)

        await self.capability_worker.speak(
            "Tracking %s at $%d baseline. The background scout will alert you on any drop."
            % (name[:40], int(price))
        )
        return True

    # -------------------------------------------------------------------------
    # Session loop
    # -------------------------------------------------------------------------

    async def run(self):
        try:
            if not self._config_ok():
                await self.capability_worker.speak(
                    "Smart Deal Scout needs a Serper API key to work. "
                    "Get a free key at serper dot dev, add it to main dot py and background dot py, then re-upload."
                )
                self.capability_worker.resume_normal_flow()
                return

            self._log("Session started. Trigger: '%s'" % self._trigger_text)

            wl = await self._load_watchlist()
            is_returning = bool(wl.get("items"))
            pending_input = None

            if self._trigger_text and len(self._trigger_text.split()) > 2:
                pending_input = self._trigger_text

            if pending_input:
                await self.capability_worker.speak(
                    "Smart Deal Scout here. Let me look into that..."
                )
            elif is_returning:
                n = len(wl["items"])
                await self.capability_worker.speak(
                    "Welcome back to Smart Deal Scout! "
                    "You're tracking %d item%s. "
                    "Say 'check deals' for updates, 'list' to see your items, or search for a new deal."
                    % (n, "s" if n != 1 else "")
                )
            else:
                await self.capability_worker.speak(
                    "Welcome to Smart Deal Scout! I can track prices and find deals by voice. "
                    "Try 'find me a deal on headphones' or 'track AirPods Pro'. What can I help you with?"
                )

            idle_count = 0
            turn = 0
            pending_guess = None

            while turn < MAX_TURNS:
                turn += 1

                if pending_input is not None:
                    user_input = pending_input
                    pending_input = None
                else:
                    user_input = await self.capability_worker.user_response()

                if not user_input or not user_input.strip():
                    idle_count += 1
                    if idle_count >= IDLE_EXIT:
                        await self.capability_worker.speak("No response detected. Goodbye!")
                        break
                    if idle_count >= IDLE_REPROMPT:
                        user_input = await self.capability_worker.run_io_loop(
                            "Still here! Say 'find a deal', 'check deals', or 'list' to get started."
                        )
                        if user_input and user_input.strip():
                            idle_count = 0
                    continue

                idle_count = 0

                if pending_guess:
                    lo = user_input.lower().strip()
                    if any(w in lo for w in ("yes", "yep", "yeah", "sure", "ok", "okay", "correct")):
                        pending_input = pending_guess
                        pending_guess = None
                        continue
                    pending_guess = None

                if not pending_guess and self._wants_exit(user_input):
                    await self.capability_worker.speak("Thanks for using Smart Deal Scout. Happy saving!")
                    break

                # Check track-from-search shortcut before full classify
                if await self._maybe_track_from_last(user_input):
                    continue

                classification = self._classify_intent(user_input)
                intent = classification.get("intent", "clarify")
                self._log("Intent: %s | '%s'" % (intent, user_input[:60]))

                if intent == "exit":
                    await self.capability_worker.speak("Goodbye! Happy saving!")
                    break

                elif intent == "search":
                    await self._handle_search(user_input)

                elif intent == "track":
                    await self._handle_track(user_input, classification.get("product"))

                elif intent == "check":
                    await self._handle_check()

                elif intent == "list":
                    await self._handle_list()

                elif intent == "remove":
                    await self._handle_remove(user_input)

                elif intent == "detail":
                    await self._handle_detail(user_input)

                else:
                    guess = self._guess_product(user_input)
                    if guess:
                        pending_guess = guess
                        await self.capability_worker.speak(
                            "I didn't quite catch that. Did you mean to search for %s? "
                            "Say yes to search, or tell me what you'd like." % guess
                        )
                    else:
                        await self.capability_worker.speak(
                            "I can help you find deals or track prices. "
                            "Try 'find a deal on' followed by a product, or 'track' a product name."
                        )

        except Exception as exc:
            self._err("Fatal: %s" % exc)
            await self.capability_worker.speak("Something went wrong. Please try again.")
        finally:
            self.capability_worker.resume_normal_flow()

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def call(self, worker: AgentWorker):
        self.worker = worker
        self.capability_worker = CapabilityWorker(self)
        self._last_results = []
        self._trigger_text = ""
        try:
            if worker.transcription and worker.transcription.strip():
                self._trigger_text = worker.transcription.strip()
        except Exception:
            pass
        if not self._trigger_text:
            try:
                if worker.last_transcription and worker.last_transcription.strip():
                    self._trigger_text = worker.last_transcription.strip()
            except Exception:
                pass
        self.worker.session_tasks.create(self.run())
