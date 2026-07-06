// SharpOdds — find a single event on Polymarket and return just the odds.
//
// A cheap DeepSeek model is used ONLY for the fuzzy natural-language match
// (turning "England vs Ghana 1" into the correct market/outcome). All odds
// numbers are computed deterministically from Polymarket's own prices, so they
// are never hallucinated by the model.

const GAMMA_SEARCH = "https://gamma-api.polymarket.com/public-search";
const DEEPSEEK_URL = "https://api.deepseek.com/chat/completions";
const DEEPSEEK_MODEL = "deepseek-chat";
const UA = "SharpOdds/1.0";

interface Market {
  id: string;
  event: string;
  question: string;
  outcomes: string[];
  prices: number[];
  bestBid: number | null;
  bestAsk: number | null;
}

// --------------------------------------------------------------------------- //
// Polymarket
// --------------------------------------------------------------------------- //
function cleanQuery(message: string): string {
  const drop = new Set(["1", "2", "x", "vs", "v", "vs."]);
  const toks = message.trim().split(/\s+/).filter(Boolean);
  const kept = toks.filter((t) => !drop.has(t.toLowerCase()));
  return kept.length ? kept.join(" ") : message.trim();
}

function toFloat(v: unknown): number | null {
  const n = parseFloat(String(v));
  return Number.isNaN(n) ? null : n;
}

async function getJson(url: string) {
  const r = await fetch(url, { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// A market that has already resolved reports one outcome at ~1 and the rest
// at ~0. Those aren't tradeable, so drop them from the candidate pool.
function isSettled(prices: number[]): boolean {
  return prices.some((p) => p >= 0.9995 || p <= 0.0005);
}

// Rank candidates by token overlap with the question so the relevant markets
// survive the cap even when a single event contains dozens of sub-markets
// (Golden Boot has 80, MVP has 51, ...).
const MAX_CANDIDATES = 40;
function rank(message: string, candidates: Market[]): Market[] {
  const q = tokens(message);
  return candidates
    .map((c) => {
      const ct = tokens(`${c.question} ${c.event} ${c.outcomes.join(" ")}`);
      let score = 0;
      for (const w of q) if (ct.has(w)) score++;
      return { c, score };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, MAX_CANDIDATES)
    .map((x) => x.c);
}

async function searchMarkets(message: string): Promise<Market[]> {
  const seen = new Set<string>();
  const candidates: Market[] = [];

  async function collect(query: string) {
    if (!query) return;
    const url = `${GAMMA_SEARCH}?${new URLSearchParams({
      q: query,
      limit_per_type: "20",
      events_status: "active",
    })}`;
    let data;
    try {
      data = await getJson(url);
    } catch {
      return;
    }
    for (const event of data.events ?? []) {
      for (const m of event.markets ?? []) {
        const mid = String(m.id);
        if (seen.has(mid) || m.closed || !m.enableOrderBook) continue;
        let outcomes: string[], prices: number[];
        try {
          outcomes = JSON.parse(m.outcomes ?? "[]");
          prices = (JSON.parse(m.outcomePrices ?? "[]") as string[]).map(Number);
        } catch {
          continue;
        }
        if (outcomes.length < 2 || outcomes.length !== prices.length) continue;
        if (isSettled(prices)) continue;
        seen.add(mid);
        candidates.push({
          id: mid,
          event: event.title ?? "",
          question: m.question ?? "",
          outcomes,
          prices,
          bestBid: toFloat(m.bestBid),
          bestAsk: toFloat(m.bestAsk),
        });
      }
    }
  }

  await collect(cleanQuery(message));
  if (!candidates.length && cleanQuery(message) !== message.trim()) {
    await collect(message.trim());
  }
  return rank(message, candidates);
}

// --------------------------------------------------------------------------- //
// DeepSeek match (with a deterministic fallback)
// --------------------------------------------------------------------------- //
function tokens(s: string): Set<string> {
  return new Set(s.toLowerCase().match(/[a-z0-9]+/g) ?? []);
}

function fallbackPick(message: string, candidates: Market[]) {
  const words = tokens(message);
  let best = 0, bestScore = -1;
  candidates.forEach((c, i) => {
    const cwords = tokens(`${c.question} ${c.event} ${c.outcomes.join(" ")}`);
    let score = 0;
    for (const w of words) if (cwords.has(w)) score++;
    if (score > bestScore) {
      best = i;
      bestScore = score;
    }
  });
  return { index: best, outcome: candidates[best].outcomes[0] };
}

async function deepseekPick(message: string, candidates: Market[]) {
  const key = Deno.env.get("DEEPSEEK_API_KEY");
  if (!key) return fallbackPick(message, candidates);

  const listing = candidates
    .map((c, i) =>
      `[${i}] event: ${c.event} | market: ${c.question} | outcomes: ${JSON.stringify(c.outcomes)}`
    )
    .join("\n");
  const system =
    "You match a sports/betting question to the correct Polymarket market from the list. " +
    "Markets span match winners, totals (over/under), awards (MVP, Golden Boot, top " +
    "scorer, most assists), player-goal milestones and head-to-heads. Most are Yes/No. " +
    "Notation: in 'A vs B 1' the 1 means the first team (A) to win, 2 the second team (B), " +
    "X a draw. Reply ONLY with compact JSON: " +
    '{"index": <int>, "outcome": "<exact outcome string the user is asking about>"}. ' +
    "Pick the single market that best answers the question; for a Yes/No prop the outcome " +
    "is usually \"Yes\" unless the user implies the negative. If nothing fits, use index -1.";
  const user = `Question: ${message}\n\nMarkets:\n${listing}`;

  try {
    const r = await fetch(DEEPSEEK_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "User-Agent": UA,
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        model: DEEPSEEK_MODEL,
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
        temperature: 0,
        response_format: { type: "json_object" },
        max_tokens: 60,
      }),
    });
    if (!r.ok) return fallbackPick(message, candidates);
    const resp = await r.json();
    const pick = JSON.parse(resp.choices[0].message.content);
    const idx = parseInt(pick.index, 10);
    if (Number.isNaN(idx) || idx < 0 || idx >= candidates.length) return null;
    return { index: idx, outcome: pick.outcome || candidates[idx].outcomes[0] };
  } catch {
    return fallbackPick(message, candidates);
  }
}

// --------------------------------------------------------------------------- //
// Odds formatting (deterministic)
// --------------------------------------------------------------------------- //
function decimal(p: number | null): number | null {
  return p && p > 0 ? Math.round(100 / p) / 100 : null;
}

function formatOdds(market: Market, outcome: string): string {
  const { outcomes, prices } = market;
  let oi = 0;
  for (let i = 0; i < outcomes.length; i++) {
    if (outcomes[i].toLowerCase() === String(outcome).toLowerCase()) {
      oi = i;
      break;
    }
  }
  const p = prices[oi];
  const otherI = oi === 0 ? 1 : 0;
  const pOther = prices[otherI];

  // Best bid/ask are quoted on outcome 0. Flip for the second outcome.
  const { bestBid: bid, bestAsk: ask } = market;
  let oBid: number | null, oAsk: number | null;
  if (oi === 0) {
    oBid = bid;
    oAsk = ask;
  } else if (bid !== null && ask !== null) {
    oBid = 1 - ask;
    oAsk = 1 - bid;
  } else {
    oBid = null;
    oAsk = null;
  }

  let line = `${market.question || market.event} — ` +
    `${outcomes[oi]} ${Math.round(p * 100)}c / ${outcomes[otherI]} ${Math.round(pOther * 100)}c`;
  const back = decimal(oAsk); // you buy at the ask
  const lay = decimal(oBid); // you sell at the bid
  if (back && lay) line += ` · back ${back.toFixed(2)} lay ${lay.toFixed(2)}`;
  return line;
}

// --------------------------------------------------------------------------- //
// Server
// --------------------------------------------------------------------------- //
let HTML = "<h1>SharpOdds</h1>";
try {
  HTML = await Deno.readTextFile(new URL("./index.html", import.meta.url));
} catch {
  // fall back to the stub above
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);

  if (url.pathname === "/api/ask" && req.method === "POST") {
    try {
      const body = await req.json().catch(() => ({}));
      const message = String(body.message ?? "").trim();
      if (!message) {
        return json({ error: "Ask about an event, e.g. 'England vs Mexico 1'." }, 400);
      }
      const candidates = await searchMarkets(message);
      if (!candidates.length) return json({ answer: "No market found." });
      const pick = await deepseekPick(message, candidates);
      if (!pick) return json({ answer: "No market found." });
      return json({ answer: formatOdds(candidates[pick.index], pick.outcome) });
    } catch (e) {
      return json({ error: `Server error: ${e}` }, 500);
    }
  }

  return new Response(HTML, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
});
