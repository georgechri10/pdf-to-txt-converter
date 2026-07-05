# SharpOdds

A dead-simple AI chat box. Ask the odds of **one** event and it finds the match
on Polymarket and replies with just the odds — no chit-chat.

```
You:  England vs Mexico 1
Bot:  Will England win on 2026-07-05 — England 37c / No 63c · back 2.63 lay 2.70
```

## How it works

1. Your question is searched against live Polymarket markets.
2. A cheap DeepSeek model (`deepseek-chat`) picks the market/outcome that matches
   your question (it understands `1 / X / 2` football shorthand).
3. The odds are computed **in Python from Polymarket's own prices** — never by
   the model — so the numbers are always accurate:
   - `Nc` = implied probability in cents (e.g. `37c` = 37%).
   - `back` = decimal odds to buy the outcome (`1 / best ask`).
   - `lay`  = decimal odds to lay it (`1 / best bid`).

If no DeepSeek key is set, it falls back to keyword matching (less accurate).

## Setup

Set one environment variable in Vercel:

| Variable | Value |
| --- | --- |
| `DEEPSEEK_API_KEY` | Your DeepSeek API key from https://platform.deepseek.com |

## Deploy

Connect the repo to [Vercel](https://vercel.com) and deploy. The Python
serverless function lives in `api/ask.py`; the UI is `index.html`.

## Run locally

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-...
python api/ask.py          # serves the API on :5000
# then open index.html (point fetch at http://localhost:5000/api/ask)
```

## Stack

- Frontend: single static `index.html` (no build step)
- Backend: Flask on Vercel Python (`api/ask.py`)
- Data: Polymarket Gamma API · Matching: DeepSeek
