import json
import re
import httpx
from datetime import datetime, timezone
from time import time

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

# =============================================================================
# SMART DEAL SCOUT — Background Price Watcher Daemon
# Runs automatically for the entire session. Proactively alerts the user
# when a tracked item's price drops significantly or hits their target.
#
# SETUP: Set SERPER_API_KEY to the same value as in main.py.
# =============================================================================

# --- Configuration (keep in sync with main.py) ---
SERPER_API_KEY = ""
SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
WATCHLIST_FILE = "deal_scout_watchlist.json"
DROP_THRESHOLD_PCT = 10
CHECK_INTERVAL_HOURS = 6
POLL_INTERVAL_SEC = 30


# =============================================================================
# Module-level helpers (duplicated from main.py — SDK single-file constraint)
# =============================================================================

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
# Background Daemon Class
# =============================================================================

class DealScoutWatcherCapability(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None
    background_daemon_mode: bool = False

    # {{register capability}}

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------

    def _log(self, msg: str):
        try:
            self.worker.editor_logging_handler.info("[DealWatcher] %s: %s" % (time(), msg))
        except Exception:
            pass

    def _err(self, msg: str):
        try:
            self.worker.editor_logging_handler.error("[DealWatcher] %s: %s" % (time(), msg))
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # File storage helpers
    # -------------------------------------------------------------------------

    async def _load_watchlist(self) -> dict:
        empty = {"items": []}
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
    # Serper API (shopping only — daemon only needs price checks)
    # -------------------------------------------------------------------------

    async def _search_shopping(self, query: str) -> list:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    SERPER_SHOPPING_URL,
                    headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                    json={"q": query, "num": 3},
                )
                resp.raise_for_status()
                return resp.json().get("shopping", [])
        except Exception as exc:
            self._err("shopping search: %s" % exc)
            return []

    # -------------------------------------------------------------------------
    # Price utilities
    # -------------------------------------------------------------------------
            
    def _best_price(self, results: list) -> tuple:
        if not results:
            return 0.0, ""
        best = None
        for item in results:
            p = _parse_price(item.get("price", "0"))
            if p > 0 and (best is None or p < best[0]):
                best = (p, item.get("source", ""))
        return best if best else (0.0, "")

    def _calc_drop(self, baseline: float, current: float) -> tuple:
        if baseline <= 0:
            return 0.0, 0.0, False
        drop_amt = baseline - current
        drop_pct = (drop_amt / baseline) * 100.0
        return round(drop_amt, 2), round(drop_pct, 1), drop_pct >= DROP_THRESHOLD_PCT

    # -------------------------------------------------------------------------
    # Alert message builder
    # -------------------------------------------------------------------------

    def _build_alert(self, item: dict, current_price: float, source: str,
                     drop_amt: float, drop_pct: float) -> str:
        name = item.get("name", "An item you're tracking")
        baseline = item.get("baseline_price", 0)
        budget = item.get("budget")
        budget_hit = budget and current_price <= budget

        if budget_hit:
            return (
                "Deal alert! %s just hit your target price — now $%d on %s, "
                "that's $%d off your baseline of $%d. Now is a great time to buy!"
                % (name, int(current_price), source, int(drop_amt), int(baseline))
            )
        return (
            "Price drop! %s dropped %.0f%% to $%d on %s — "
            "down $%d from your $%d baseline."
            % (name, drop_pct, int(current_price), source, int(drop_amt), int(baseline))
        )

    # -------------------------------------------------------------------------
    # Main watcher loop
    # -------------------------------------------------------------------------

    async def watcher_loop(self):
        self._log("Watcher started")

        while True:
            try:
                await self.worker.session_tasks.sleep(POLL_INTERVAL_SEC)

                if not SERPER_API_KEY.strip():
                    continue

                wl = await self._load_watchlist()
                items = wl.get("items", [])
                if not items:
                    continue

                changed = False

                for item in items:
                    last_checked = item.get("last_checked", "")
                    if _hours_since(last_checked) < CHECK_INTERVAL_HOURS:
                        continue

                    query = item.get("search_query", "%s buy price" % item.get("name", ""))
                    self._log("Checking: %s" % item.get("name", ""))

                    results = await self._search_shopping(query)
                    if not results:
                        continue

                    current_price, source = self._best_price(results)
                    if current_price <= 0:
                        continue

                    baseline = item.get("baseline_price", 0)
                    drop_amt, drop_pct, is_significant = self._calc_drop(baseline, current_price)
                    budget = item.get("budget")
                    budget_hit = budget and current_price <= budget

                    item["last_price"] = current_price
                    item["last_checked"] = datetime.now(timezone.utc).isoformat()
                    item["source"] = source
                    if current_price < item.get("lowest_seen", baseline):
                        item["lowest_seen"] = current_price
                    changed = True

                    if is_significant or budget_hit:
                        alert_msg = self._build_alert(item, current_price, source, drop_amt, drop_pct)
                        self._log("Alerting: %s" % alert_msg)
                        try:
                            await self.capability_worker.send_interrupt_signal()
                            await self.capability_worker.speak(alert_msg)
                        except Exception as exc:
                            self._err("Alert speak failed: %s" % exc)

                if changed:
                    await self._save_watchlist(wl)

            except Exception as exc:
                self._err("Watcher loop error: %s" % exc)

    # -------------------------------------------------------------------------
    # Entry point — background daemon signature
    # -------------------------------------------------------------------------

    def call(self, worker: AgentWorker, background_daemon_mode: bool):
        # worker and background_daemon_mode must be set BEFORE CapabilityWorker(self)
        self.worker = worker
        self.background_daemon_mode = background_daemon_mode
        self.capability_worker = CapabilityWorker(self)
        self.worker.session_tasks.create(self.watcher_loop())
