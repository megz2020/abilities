# Smart Deal Scout — Implementation Plan

## Overview

**Smart Deal Scout** is a voice-first price tracking and deal-finding ability for OpenHome. Users can track product prices, get deal alerts, search for the best prices, and receive a daily deal briefing — all by voice.

**Problem:** Everyone shops online, but price tracking requires browser extensions, apps, or manual checking. Voice is frictionless: "Track AirPods Pro" while you're thinking about it, "Any deals today?" while making coffee.

**Gap:** OpenHome has 76 abilities — zero cover shopping intelligence, price tracking, or deal discovery. `grocery-list-manager` tracks *what* to buy; Smart Deal Scout tracks *when* to buy and *at what price*.

---

## Architecture

**Category: Interactive Combined** (main.py + background.py)

```
┌──────────────────────────────────────────────────────────────────┐
│                      SMART DEAL SCOUT                            │
│               Interactive Combined Ability                       │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │  main.py  (Triggered by voice hotword)                  │     │
│  │                                                         │     │
│  │  USER VOICE INPUT                                       │     │
│  │       │                                                 │     │
│  │       ▼                                                 │     │
│  │  Intent Classifier (keyword + LLM) → 7 intents         │     │
│  │       │                                                 │     │
│  │       ├──→ TRACK   → search price → save to JSON       │     │
│  │       ├──→ SEARCH  → Serper API → summarize → speak    │     │
│  │       ├──→ CHECK   → re-search all items → report      │     │
│  │       ├──→ LIST    → read JSON → speak watchlist        │     │
│  │       ├──→ REMOVE  → delete from JSON                  │     │
│  │       ├──→ DETAIL  → drill into last results           │     │
│  │       └──→ EXIT    → goodbye + resume_normal_flow()    │     │
│  └──────────────────────┬────────────────────────────────┘     │
│                         │ reads/writes                          │
│                         ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │  deal_scout_watchlist.json  (persistent file storage)   │     │
│  │  {items: [...], settings: {...}}                        │     │
│  └─────────────────────────────────────────────────────────┘     │
│                         ▲ reads/writes                           │
│                         │                                        │
│  ┌──────────────────────┴────────────────────────────────┐      │
│  │  background.py  (Auto-starts at session, runs always) │      │
│  │                                                       │      │
│  │  while True (poll every 30s):                         │      │
│  │    read watchlist.json                                │      │
│  │    for item where last_checked > CHECK_INTERVAL:      │      │
│  │      Serper API → get current price                   │      │
│  │      if drop > 10% OR budget hit:                     │      │
│  │        send_interrupt_signal()                        │      │
│  │        speak("AirPods dropped 20%! Now $159 on...")   │      │
│  │      update last_price + last_checked in JSON         │      │
│  └───────────────────────────────────────────────────────┘      │
│                                                                  │
│  EXTERNAL APIS                                                   │
│  ┌──────────────────────────┐                                    │
│  │  Serper API              │ main.py  → on-demand search        │
│  │  serper.dev (free 2500/mo)│ background.py → proactive polls  │
│  └──────────────────────────┘                                    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Why Background Daemon Changes Everything

Without background.py, the user must ask "any deals?" manually — the value is reactive.
With background.py, OpenHome **proactively interrupts** the conversation:

> *"Hey, I just noticed AirPods Pro dropped 20% to $159 on Amazon — that's below your $170 target!"*

This transforms Smart Deal Scout from a lookup tool into a true deal **scout**.

---

## Intent Classification

### 7 Intents

| Intent | Trigger Examples | Action |
|--------|-----------------|--------|
| **track** | "Track AirPods Pro", "Watch PlayStation 5 price", "Alert me when Nike shoes go under $80" | Parse product name + optional budget → save to watchlist → confirm |
| **search** | "Find me the best deal on a coffee maker", "What's the cheapest noise-canceling headphones?" | Serper search → LLM ranks by price/value → speak top 3 |
| **check** | "Check my deals", "Any price drops?", "Deal update" | Re-search all tracked items → compare vs baseline → report drops |
| **list** | "What am I tracking?", "Show my watchlist", "List tracked items" | Read watchlist → speak summary |
| **remove** | "Stop tracking AirPods", "Remove headphones from my list" | Match item by name → delete → confirm |
| **detail** | "Tell me more about that", "What about the second one?" | Drill into last search result for specs/reviews |
| **exit** | "Stop", "Done", "Bye", "Thanks" | Farewell → resume_normal_flow |

### Classification Strategy (2-layer, proven in health-supplement-search)

```
Layer 1: Keyword matching (fast, no API call)
  - "track" / "watch" / "alert me" / "notify me"  → track
  - "find" / "search" / "best deal" / "cheapest"   → search
  - "check" / "any deals" / "price drops"           → check
  - "list" / "watchlist" / "what am I tracking"     → list
  - "remove" / "stop tracking" / "delete"           → remove
  - EXIT_WORDS set                                  → exit

Layer 2: LLM fallback (handles STT garbles + ambiguity)
  - Only called if keyword matching fails
  - Returns JSON: {"intent": "track|search|check|list|remove|detail|exit|clarify", "product": "...", "budget": null}
  - Retry once on bad JSON, fallback to "clarify"
```

---

## Data Model

### Watchlist Storage (via `capability_worker` context KV store)

```python
# Key: "deal_scout_watchlist"
# Value:
{
    "items": [
        {
            "id": "airpods-pro-1710000000",       # slug + timestamp
            "name": "AirPods Pro",                 # user's original query
            "search_query": "AirPods Pro price",   # optimized for Serper
            "budget": 199.99,                      # null if no budget set
            "baseline_price": 249.00,              # price at time of tracking
            "lowest_seen": 219.00,                 # lowest price ever seen
            "last_checked": "2026-03-16T10:00:00", # ISO timestamp
            "last_price": 229.00,                  # most recent price found
            "source": "Amazon",                    # where the price was found
            "added_at": "2026-03-10T08:30:00"      # when user started tracking
        }
    ],
    "settings": {
        "drop_threshold_pct": 10,                  # alert when price drops 10%+
        "max_items": 15                            # watchlist cap
    }
}
```

### Why File Storage (not KV store)?

- background.py and main.py **must share state** — file storage is the SDK-recommended coordination pattern (see Alarm template)
- `check_if_file_exists` / `read_file` / `write_file` / `delete_file` — all async-native
- KV store methods are synchronous; file API is async (natural for daemon loops)
- Alarm template proves this pattern: main.py writes alarms.json, background.py reads it
- Always delete-then-write to avoid JSON append corruption (SDK rule)

---

## API Strategy

### Primary: Serper API (Google Search)

**Why Serper:**
- Already proven in health-supplement-search (same codebase)
- Free tier: 2,500 searches/month (generous for personal tracking)
- Returns structured `shopping` results with prices, titles, sources
- No auth complexity (single API key)

**Serper Shopping Endpoint:**
```python
# POST https://google.serper.dev/shopping
# Headers: {"X-API-KEY": "...", "Content-Type": "application/json"}
# Body: {"q": "AirPods Pro price", "num": 5}
#
# Response:
# {
#   "shopping": [
#     {
#       "title": "Apple AirPods Pro (2nd Generation)",
#       "source": "Amazon",
#       "link": "https://amazon.com/...",
#       "price": "$189.99",
#       "rating": 4.7,
#       "ratingCount": 12345,
#       "imageUrl": "https://...",
#       "delivery": "Free delivery"
#     }
#   ]
# }
```

**Also use standard search as fallback:**
```python
# POST https://google.serper.dev/search
# For when shopping endpoint returns no results
# Parse prices from organic snippets using LLM
```

### Rate Limiting Strategy

- Max 15 tracked items (configurable)
- "Check all" searches serially (not parallel) to avoid burst
- Each check = 1 API call per item (15 items = 15 calls)
- Daily check = 15 calls/day × 30 days = 450 calls/month (well within 2,500 free)
- Manual searches are unbounded but user-gated (voice friction limits abuse)

---

## Conversation Flow

### Flow 1: First-Time User

```
OpenHome: "Welcome to Smart Deal Scout! I can track prices and find deals for you.
           Try saying 'track AirPods Pro' or 'find me a deal on headphones'."
User:     "Track AirPods Pro"
OpenHome: "Let me look up the current price for AirPods Pro..."
          [Serper shopping search]
OpenHome: "I found AirPods Pro at $189 on Amazon and $199 at Best Buy.
           I'll track this at the $189 baseline. Want to set a target price,
           or should I alert you on any significant drop?"
User:     "Alert me if it goes under $170"
OpenHome: "Got it! I'm tracking AirPods Pro with a target of $170.
           I'll let you know when there's a price drop.
           Want to track something else or search for a deal?"
```

### Flow 2: Daily Deal Check

```
User:     "Any deals today?"
OpenHome: "Let me check your 3 tracked items..."
          [Serper search for each item]
OpenHome: "Good news! AirPods Pro dropped to $179, that's 5% below your baseline of $189.
           Your Nike Air Max are still at $130, no change.
           And the PS5 is actually up $10 to $509. Want details on any of these?"
```

### Flow 3: Search for a Deal

```
User:     "Find me the best deal on a coffee maker"
OpenHome: "Let me search for coffee maker deals..."
          [Serper shopping search]
OpenHome: "Here are the top 3 deals I found:
           1. Keurig K-Mini at $59 on Amazon, rated 4.5 stars
           2. Ninja DualBrew at $99 at Walmart, rated 4.6 stars
           3. Hamilton Beach FlexBrew at $44 at Target, rated 4.3 stars
           Want details on any of these, or should I track one?"
User:     "Track the Ninja one"
OpenHome: "Tracking Ninja DualBrew at $99 baseline. I'll alert you on any price drop."
```

### Flow 4: Manage Watchlist

```
User:     "What am I tracking?"
OpenHome: "You're tracking 3 items:
           1. AirPods Pro — baseline $189, currently $179, target $170
           2. Nike Air Max — baseline $130, no change
           3. PS5 — baseline $499, currently $509, no target set
           Want to remove any, or add something new?"
User:     "Remove the PS5"
OpenHome: "PS5 removed from your watchlist. You're now tracking 2 items."
```

### Flow 5: STT Garble Recovery

```
User:     "track err pods" (garbled "AirPods")
OpenHome: "Did you mean AirPods? Say yes to track that, or tell me the product name again."
User:     "Yes"
OpenHome: "Let me look up the current price for AirPods..."
```

---

## Implementation Plan (File Structure)

```
community/smart-deal-scout/
├── __init__.py          # empty
├── main.py              # interactive: user triggers, session loop (~600 lines)
├── background.py        # daemon: proactive price watcher (~250 lines)
└── README.md            # setup instructions + examples
```

### background.py Structure

```python
# === IMPORTS ===
import json
import re
import httpx
from datetime import datetime, timezone
from time import time

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

# === CONFIGURATION (must match main.py) ===
SERPER_API_KEY = ""
SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
WATCHLIST_FILE = "deal_scout_watchlist.json"
CHECK_INTERVAL_HOURS = 6     # re-query Serper after 6h per item
DROP_THRESHOLD_PCT = 10      # alert on 10%+ drop
POLL_INTERVAL_SEC = 30       # daemon wakes every 30s (cheap timestamp check)

class DealScoutWatcherCapability(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None
    background_daemon_mode: bool = False

    # {{register capability}}

    # --- Logging ---
    def _log(self, msg): ...
    def _err(self, msg): ...

    # --- File helpers (same as main.py, duplicated by SDK design) ---
    async def _load_watchlist(self) -> dict: ...
    async def _save_watchlist(self, data: dict): ...

    # --- Serper API (subset — shopping only) ---
    async def _search_shopping(self, query: str) -> list: ...

    # --- Price utilities ---
    def _parse_price(self, text: str) -> float: ...
    def _extract_best_price(self, results: list) -> tuple: ...
    def _calculate_drop(self, baseline: float, current: float) -> tuple: ...

    # --- Main watcher loop ---
    async def watcher_loop(self):
        # while True:
        #   1. session_tasks.sleep(POLL_INTERVAL_SEC)
        #   2. load watchlist.json (return early if empty or no file)
        #   3. for each item: check if time_since_last_checked > CHECK_INTERVAL_HOURS
        #   4. if due: Serper search → compare price
        #   5. if significant drop OR budget hit:
        #        send_interrupt_signal()
        #        speak alert message
        #   6. update item last_price + last_checked in watchlist
        #   7. save watchlist

    def call(self, worker: AgentWorker, background_daemon_mode: bool):
        # NOTE: set worker + background_daemon_mode BEFORE CapabilityWorker(self)
        self.worker = worker
        self.background_daemon_mode = background_daemon_mode
        self.capability_worker = CapabilityWorker(self)
        self.worker.session_tasks.create(self.watcher_loop())
```

### main.py Structure

```python
# === IMPORTS ===
import asyncio
import json
import re
import httpx

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

# === CONFIGURATION ===
SERPER_API_KEY = ""                    # Required: free at serper.dev
SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
SERPER_SEARCH_URL = "https://google.serper.dev/search"
MAX_TRACKED_ITEMS = 15
MAX_SEARCH_RESULTS = 5
DROP_THRESHOLD_PCT = 10                # alert on 10%+ drops
MAX_TURNS = 20
IDLE_REPROMPT = 2
IDLE_EXIT = 3
WATCHLIST_KEY = "deal_scout_watchlist"

# === EXIT WORDS ===
EXIT_WORDS = {"stop", "exit", "quit", "done", "bye", "goodbye", ...}

# === HELPER: LLM fence stripping ===
def _strip_llm_fences(text): ...

# === HELPER: Price parsing ===
def _parse_price(text): ...
# Extract numeric price from "$189.99" or "189 dollars" etc.

# === HELPER: Generate item ID ===
def _make_item_id(name): ...
# slug + timestamp

# === MAIN CLASS ===
class SmartDealScoutCapability(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None

    _last_results: list = None          # last search results for detail drill-down
    _trigger_text: str = ""             # text that triggered the ability

    # {{register capability}}

    # --- Logging ---
    def _log(self, msg): ...
    def _err(self, msg): ...

    # --- Config check ---
    def _config_ok(self) -> bool: ...
    # Check SERPER_API_KEY is set

    # --- Storage helpers ---
    def _load_watchlist(self) -> dict: ...
    # get_single_key(WATCHLIST_KEY) → return default if None

    def _save_watchlist(self, data: dict): ...
    # Safe create-or-update pattern

    # --- Serper API ---
    async def _search_shopping(self, query: str, num: int = 5) -> list: ...
    # POST to shopping endpoint, return structured results

    async def _search_web(self, query: str, num: int = 5) -> list: ...
    # Fallback: standard search endpoint

    async def _search_product(self, query: str) -> list: ...
    # Try shopping first → fall back to web search
    # Normalize results to: {title, price, source, link, rating}

    # --- Price utilities ---
    def _extract_best_price(self, results: list) -> tuple: ...
    # Returns (price_float, source_str, title_str) from results

    def _calculate_drop(self, baseline: float, current: float) -> tuple: ...
    # Returns (drop_amount, drop_pct, is_significant)

    # --- Intent classification ---
    def _classify_intent(self, user_input: str) -> dict: ...
    # Layer 1: keyword match
    # Layer 2: LLM fallback
    # Returns: {"intent": str, "product": str|None, "budget": float|None}

    def _wants_exit(self, user_input: str) -> bool: ...
    # Keyword-first + LLM for short ambiguous inputs

    # --- STT garble recovery ---
    def _guess_product(self, user_input: str) -> str: ...
    # LLM guesses what product the user meant

    # --- Intent handlers ---
    async def _handle_track(self, product: str, budget: float = None): ...
    # 1. Search current price
    # 2. Speak findings
    # 3. Ask for target price (optional)
    # 4. Save to watchlist
    # 5. Confirm

    async def _handle_search(self, query: str): ...
    # 1. Search via Serper
    # 2. LLM summarize top 3 for voice
    # 3. Store results in _last_results for drill-down
    # 4. Offer to track any result

    async def _handle_check(self): ...
    # 1. Load watchlist
    # 2. For each item: search current price
    # 3. Compare vs baseline
    # 4. Speak: drops, unchanged, increases
    # 5. Update last_price + last_checked in store

    async def _handle_list(self): ...
    # 1. Load watchlist
    # 2. Speak summary of all items
    # 3. Offer to remove or add

    async def _handle_remove(self, product: str): ...
    # 1. Fuzzy match product name in watchlist
    # 2. Confirm with user
    # 3. Remove + save
    # 4. Speak confirmation

    async def _handle_detail(self, user_input: str): ...
    # 1. Match "first", "second", "third" or product name
    # 2. Give fuller detail from _last_results
    # 3. Offer to track

    # --- Main session loop ---
    async def run(self): ...
    # 1. Config check
    # 2. Extract trigger text
    # 3. Welcome message (first-time vs returning)
    # 4. Session loop (MAX_TURNS):
    #    a. Get input (pending_input or user_response)
    #    b. Idle detection (IDLE_REPROMPT, IDLE_EXIT)
    #    c. Exit check
    #    d. Classify intent
    #    e. Route to handler
    # 5. resume_normal_flow() in finally block

    def call(self, worker: AgentWorker): ...
    # Standard boilerplate: set worker, capability_worker
    # Extract trigger_text from transcription
    # Launch run() via session_tasks.create()
```

---

## Edge Cases & Error Handling

| Scenario | Handling |
|----------|----------|
| Serper API down / timeout | Speak "I'm having trouble checking prices right now. Try again in a moment." + log error |
| No shopping results found | Fall back to web search → if still nothing: "I couldn't find pricing for that product. Try a more specific name." |
| Watchlist full (15 items) | "Your watchlist is full at 15 items. Remove one first, or say 'list' to see what you're tracking." |
| Duplicate tracking attempt | "You're already tracking AirPods Pro. Want to update the target price?" |
| Price parse failure | LLM fallback to extract price from free-text snippet |
| STT garbles product name | Guess-and-confirm flow (proven in health-supplement-search) |
| User says "yes" / "track that" after search | Context-aware: track the best result from last search |
| Empty watchlist on "check" | "You're not tracking anything yet. Try 'track' followed by a product name." |
| SERPER_API_KEY not set | Speak config error + point to README on first run |
| Context storage errors | Try/except with fallback to empty watchlist |

---

## LLM Prompt Templates

### Intent Classification (Layer 2)
```
Classify this voice input into one of these intents:
- track: user wants to track/watch a product's price
- search: user wants to find deals or compare prices right now
- check: user wants to see updates on tracked items
- list: user wants to see what they're tracking
- remove: user wants to stop tracking something
- detail: user wants more info about a previously shown result
- exit: user wants to leave
- clarify: input is unclear

Return ONLY JSON: {"intent": "...", "product": "..." or null, "budget": number or null}
Input: "{user_input}"
```

### Product Name Recovery (STT garble)
```
This voice input may be garbled by speech recognition.
The user is trying to name a product they want to track or search for.
Reply with only the most likely product name (2-6 words).
If you cannot detect a product, reply: NONE
Input: "{user_input}"
```

### Shopping Results Summary
```
The user asked: "{query}"

Top shopping results:
{results_text}

Give a friendly 3-4 sentence voice response listing the top 3 deals.
Mention: product name, price, store, and rating if available.
End by asking if they want details or to track any item.
Keep it conversational. No markdown. No URLs.
```

### Deal Check Summary
```
The user has these tracked items with price changes:
{items_with_changes}

Give a concise voice update:
- Lead with good news (price drops) if any
- Mention unchanged items briefly
- Note any price increases as a caution
Keep it to 3-5 sentences. Be enthusiastic about drops. No markdown.
```

---

## Validator Compliance Checklist

| Rule | Status | Notes |
|------|--------|-------|
| Single `main.py` | ✅ | All logic in one file |
| `README.md` present | ✅ | Full setup + examples |
| `__init__.py` present | ✅ | Empty file |
| `worker: AgentWorker = None` | ✅ | Class attribute |
| `capability_worker: CapabilityWorker = None` | ✅ | Class attribute |
| `#{{register capability}}` tag | ✅ | In class body |
| `resume_normal_flow()` called | ✅ | In finally block |
| No `print()` | ✅ | Use `_log()` / `_err()` |
| No `asyncio.sleep()` | ✅ | Not needed (no timers) |
| No `asyncio.create_task()` | ✅ | Use `session_tasks.create()` |
| No `open()` | ✅ | Use context KV store |
| No `exec()` / `eval()` | ✅ | N/A |
| No `pickle` / `assert` | ✅ | N/A |
| No banned imports | ✅ | Only: asyncio, json, re, httpx |
| No `urllib` (even in comments) | ✅ | CI raw scan; avoid the word entirely |
| No `getattr` (even in comments) | ✅ | CI raw scan |
| Hyphenated folder name | ✅ | `smart-deal-scout` |
| Error handling on all API calls | ✅ | try/except on every httpx call |
| No hardcoded API keys | ✅ | Empty string placeholders |

---

## Testing Plan

### Manual Testing (OpenHome Live Editor)

- [ ] **Config error**: Leave SERPER_API_KEY empty → should speak setup instructions
- [ ] **Track product**: "Track AirPods Pro" → should search, show price, save to watchlist
- [ ] **Track with budget**: "Track Nike shoes under $80" → should parse budget + save
- [ ] **Search deal**: "Find me a deal on coffee maker" → should show top 3 results
- [ ] **Check deals**: "Any deals today?" → should check all tracked items, report changes
- [ ] **List watchlist**: "What am I tracking?" → should list all tracked items
- [ ] **Remove item**: "Stop tracking AirPods" → should confirm + remove
- [ ] **Detail drill-down**: "Tell me more about the first one" → should give details
- [ ] **Track from search**: "Track that one" after search → should save top result
- [ ] **STT garble**: Say garbled product name → should guess + confirm
- [ ] **Duplicate tracking**: Track same product twice → should offer to update
- [ ] **Empty watchlist**: "Check deals" with nothing tracked → should guide user
- [ ] **Full watchlist**: Add 15 items → should warn on 16th attempt
- [ ] **Off-topic**: "What's the weather?" → should redirect politely
- [ ] **Exit**: "bye", "done", "thanks" → should exit cleanly
- [ ] **Idle timeout**: Say nothing 3 times → should exit gracefully
- [ ] **Validator**: `python validate_ability.py community/smart-deal-scout` → zero errors

---

## Implementation Order

### Phase 1: Core (MVP)
1. Scaffold: `__init__.py`, `README.md`, class boilerplate
2. Config + Serper API integration (shopping + web fallback)
3. Intent classification (keyword layer + LLM fallback)
4. `_handle_search` — find deals by voice
5. Session loop + exit/idle handling
6. Validate with `validate_ability.py`

### Phase 2: Tracking
7. Storage helpers (load/save watchlist via context KV)
8. `_handle_track` — save product with baseline price + optional budget
9. `_handle_list` — speak watchlist summary
10. `_handle_remove` — delete tracked item
11. `_handle_check` — re-search all items, compare prices, report

### Phase 3: Polish
12. `_handle_detail` — drill into search results
13. STT garble recovery (guess-and-confirm)
14. Context-aware tracking ("track that one" after search)
15. First-time vs returning user greeting
16. Edge case hardening (duplicates, full watchlist, no results)

### Phase 4: Submission
17. Final README with setup instructions + example conversations
18. Run validator + fix any issues
19. Record Loom demo
20. Open PR against `dev`

---

## Estimated Complexity

| Component | Lines (est.) | Complexity |
|-----------|-------------|------------|
| Imports + config + constants | ~50 | Low |
| Helper functions | ~40 | Low |
| Serper API (shopping + web) | ~80 | Medium |
| Intent classification | ~60 | Medium |
| Storage helpers | ~40 | Low |
| Track handler | ~50 | Medium |
| Search handler | ~40 | Medium |
| Check handler | ~60 | Medium |
| List handler | ~25 | Low |
| Remove handler | ~35 | Low |
| Detail handler | ~35 | Low |
| STT recovery | ~25 | Low |
| Session loop + exit | ~60 | Medium |
| **Total** | **~600** | **Medium** |

---

## Key Design Decisions

### 1. Serper Shopping API (not web scraping or Amazon API)
- **Why**: Free 2,500/month, structured JSON, proven in this codebase, no auth complexity
- **Tradeoff**: Results come from Google Shopping (multi-retailer), not a single store
- **Alternative considered**: Amazon Product API (requires affiliate account, complex auth)

### 2. File Storage (not KV store)
- **Why**: background.py and main.py must share state; file storage is the SDK-recommended coordination pattern (proven in Alarm template)
- **Tradeoff**: Requires async delete-then-write discipline for JSON safety
- **Alternative considered**: KV store (synchronous methods make it awkward in daemon async loops; also doesn't support background daemon access)

### 3. 15-item watchlist cap
- **Why**: 15 items × daily check = 15 Serper calls/day = 450/month (within free tier)
- **Tradeoff**: Power users might want more
- **Configurable**: `MAX_TRACKED_ITEMS` constant at top of file

### 4. No proactive notifications (V1)
- **Why**: OpenHome abilities are triggered by voice; there's no push notification API in the SDK
- **Future**: Could integrate with n8n-commander or webhook for scheduled checks
- **V1 workaround**: User triggers "any deals?" manually — still valuable

### 5. Shopping endpoint first, web search fallback
- **Why**: Shopping endpoint returns structured price data; web search requires LLM price extraction
- **Tradeoff**: Some products (niche/used) may not appear in shopping results
- **Fallback**: Standard search → LLM extracts price from snippets
