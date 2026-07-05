# SharpOdds

A dead-simple AI chat box. Ask the odds of **one** event and it finds the match
on Polymarket and replies with just the odds — no chit-chat.

```
You:  England vs Mexico 1
Bot:  Will England win on 2026-07-05? — Yes 38c / No 63c · back 2.63 lay 2.70
```

## How it works

1. Your question is searched against live Polymarket markets.
2. A cheap DeepSeek model (`deepseek-chat`) picks the market/outcome that matches
   your question (it understands `1 / X / 2` football shorthand).
3. The odds are computed **in code from Polymarket's own prices** — never by the
   model — so the numbers are always accurate:
   - `Nc` = implied probability in cents (e.g. `38c` = 38%).
   - `back` = decimal odds to buy the outcome (`1 / best ask`).
   - `lay`  = decimal odds to lay it (`1 / best bid`).

If no DeepSeek key is set, it falls back to keyword matching (less accurate).

## Stack

- Single TypeScript file (`main.ts`) — serves the UI and the `/api/ask` endpoint.
- Frontend: minimal static `index.html`, no framework, no build step.
- Runtime: [Deno](https://deno.com). Hosting: **Deno Deploy** (free tier, no card).

## Run locally

```bash
export DEEPSEEK_API_KEY=sk-...
deno task start          # http://localhost:8000
```

## Deploy (Deno Deploy — free)

```bash
deno install -A -g -n deployctl jsr:@deno/deployctl
deployctl deploy --project=sharpodds --entrypoint=main.ts --token=$DENO_DEPLOY_TOKEN
```

Set the `DEEPSEEK_API_KEY` environment variable on the project in the
[Deno Deploy dashboard](https://dashboard.deno.com) (Settings → Environment
Variables), then redeploy.
