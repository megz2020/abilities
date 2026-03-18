# Smart Deal Scout

![Community](https://img.shields.io/badge/OpenHome-Community-orange?style=flat-square)
![Author](https://img.shields.io/badge/Author-@megz2020-lightgrey?style=flat-square)
![Category](https://img.shields.io/badge/Category-Interactive%20Combined-blue?style=flat-square)

## What It Does

Smart Deal Scout is a voice-first price tracking and deal-finding ability. Track products you want to buy, search for the best deals on anything, and get **proactive alerts** when a price drops — all by voice.

The background daemon runs automatically throughout your session and interrupts you the moment a tracked item hits your target price.

## Suggested Trigger Phrases

- "find me a deal"
- "track a price"
- "deal scout"
- "check my deals"
- "price tracker"

## Setup

### 1. Get a free Serper API key

- Go to [serper.dev](https://serper.dev)
- Sign up for a free account (2,500 searches/month, no credit card)
- Copy your API key

### 2. Add your key to both files

Open `main.py` and `background.py` and set `SERPER_API_KEY` at the top of each:

```python
SERPER_API_KEY = "your-key-here"
```

Both files must have the same key.

### 3. Upload to OpenHome

- Zip the `smart-deal-scout/` folder
- Go to OpenHome Dashboard → Abilities → Add Custom Ability
- Upload the zip
- Set trigger phrases in the dashboard
- Select category: **Interactive Combined**

## How It Works

**main.py** handles direct voice interactions:
- Track a product and save it to your watchlist
- Search for deals right now
- Check price updates on all tracked items
- List, remove, or get details on tracked products

**background.py** runs silently in the background:
- Checks each tracked item every 6 hours via Serper
- If a price drops 10%+ or hits your target, it interrupts and alerts you immediately
- Works even while you're using other abilities

## Conversation Examples

### Track a product

> **User:** "Track AirPods Pro"
>
> **Scout:** "Looking up the current price for AirPods Pro... Found it at $189 on Amazon. I'll set that as your baseline. Do you have a target price in mind?"
>
> **User:** "Alert me under $160"
>
> **Scout:** "Done! Tracking AirPods Pro at $189 baseline, target $160. I'll let you know the moment it drops."

### Search for deals

> **User:** "Find me the best deal on noise-canceling headphones"
>
> **Scout:** "Searching now... Here are the top 3: Sony WH-1000XM5 at $279 on Amazon rated 4.7 stars, Bose QC45 at $249 at Best Buy rated 4.6 stars, Anker Q45 at $55 on Amazon rated 4.4 stars. Want details on any of these, or should I track one?"

### Proactive alert (from background daemon)

> *(While you're doing something else)*
>
> **Scout:** "Deal alert! AirPods Pro just dropped to $155 on Amazon — that's $34 off your baseline and below your $160 target!"

### Check all tracked items

> **User:** "Any deals today?"
>
> **Scout:** "Checking your 3 tracked items... AirPods Pro dropped 8% to $174. Nike Air Max unchanged at $130. PS5 is up $10 to $509. Want details on anything?"

### Manage your list

> **User:** "What am I tracking?"
>
> **Scout:** "You're tracking 3 items: AirPods Pro at $189 baseline, now $174, target $160. Nike Air Max at $130, no change. PS5 at $499, now $509. Want to remove any or add something new?"

## Rate Limits

With the free Serper tier (2,500 searches/month):

| Use | API calls |
|-----|-----------|
| Background checks (15 items, every 6h) | ~1,800/month |
| Manual searches | ~30/month |
| Manual "check deals" | ~90/month |
| **Total estimate** | **~1,920/month** |

Well within the free tier. Increase `CHECK_INTERVAL_HOURS` in both files to reduce calls further.

## Configuration

Both `main.py` and `background.py` share these constants at the top:

| Constant | Default | Description |
|----------|---------|-------------|
| `SERPER_API_KEY` | `""` | Required — get free at serper.dev |
| `MAX_TRACKED_ITEMS` | `15` | Max items in your list |
| `DROP_THRESHOLD_PCT` | `10` | Alert when price drops this % |
| `CHECK_INTERVAL_HOURS` | `6` | How often daemon re-checks each item |
| `POLL_INTERVAL_SEC` | `30` | How often daemon wakes (timestamp check only) |

## Validator

```bash
python validate_ability.py community/smart-deal-scout
```

Expected: ✅ All checks passed
