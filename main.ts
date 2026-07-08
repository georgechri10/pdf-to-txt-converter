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

// Broadcast/novelty "what will the commentators say" markets exist for every
// match and are stuffed with the same team names as the real match-winner
// market. Their sheer volume (20-30 per game) lets them out-score the market
// a user actually wants under plain token overlap, burying it. These are
// never what "the odds of an event" means, so drop the whole family.
function isNoveltyMarket(eventTitle: string, question: string): boolean {
  return /announcers? say|broadcast(er)?s? say/i.test(
    `${eventTitle} ${question}`,
  );
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

// Use the model's sports knowledge to turn a loose question into concrete
// search queries, adding the league/competition it can infer. This bridges
// ambiguous phrasings ("who wins the World Series" -> "MLB World Series
// champion") to Polymarket's text search, which is otherwise easily misled.
async function expandQueries(message: string): Promise<string[]> {
  const base = cleanQuery(message);
  const key = Deno.env.get("DEEPSEEK_API_KEY");
  if (!key) return [base];
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
          {
            role: "system",
            content:
              "Rewrite a sports betting question into up to 3 short search queries for a " +
              "prediction market's fuzzy text search. ALWAYS include one bare query that " +
              "is ONLY the competitor names (teams/players) with the full official name " +
              "(e.g. 'USA' -> 'United States') and nothing else — no 'winner', 'vs', " +
              "'over/under', 'to advance', or other descriptive words, since those extra " +
              "words can bury the exact match under unrelated results. Then, separately, " +
              "add up to 2 more queries with the league/competition you can infer (e.g. " +
              "'World Series' -> 'MLB World Series champion'; 'the Ashes' -> 'cricket " +
              "Ashes winner') to help find props/totals/awards. " +
              'Reply ONLY with compact JSON {"queries": ["..."]}.',
          },
          { role: "user", content: message },
        ],
        temperature: 0,
        response_format: { type: "json_object" },
        max_tokens: 80,
      }),
    });
    if (!r.ok) return [base];
    const resp = await r.json();
    const parsed = JSON.parse(resp.choices[0].message.content);
    const qs = (parsed.queries ?? []).filter(
      (s: unknown) => typeof s === "string" && s.trim(),
    ) as string[];
    return qs.length ? Array.from(new Set([...qs, base])) : [base];
  } catch {
    return [base];
  }
}

// Match-level prop/totals/advance markets live under a sibling event named
// "<slug>-more-markets" that Polymarket's text search does not surface no
// matter how the query is phrased (verified against several live matches).
// The base event IS found by search, so once we have its slug we can fetch
// the sibling directly instead of guessing further search phrasings.
const PROP_HINT =
  /\b(over|under|o\/u|total|totals|corner|card|handicap|spread|advance|prop|props|both teams|btts)\b/i;

async function fetchMoreMarketsEvent(slug: string) {
  try {
    const data = await getJson(
      `https://gamma-api.polymarket.com/events?${new URLSearchParams({
        slug: `${slug}-more-markets`,
      })}`,
    );
    return Array.isArray(data) ? data[0] : null;
  } catch {
    return null;
  }
}

type GammaEvent = { title?: string; slug?: string; markets?: unknown[] };

// Fetches run concurrently (for speed), but every result is merged into the
// candidate list in a fixed, query-defined order — never in network-arrival
// order. Otherwise which duplicate-scoring market ends up first (and so gets
// shown to the LLM first) varies from request to request, which combined
// with the model's own point-in-time variance made identical questions
// occasionally resolve to different — or no — markets.
async function fetchEvents(query: string): Promise<GammaEvent[]> {
  if (!query) return [];
  const url = `${GAMMA_SEARCH}?${new URLSearchParams({
    q: query,
    limit_per_type: "20",
    events_status: "active",
  })}`;
  try {
    const data = await getJson(url);
    return data.events ?? [];
  } catch {
    return [];
  }
}

async function searchMarkets(message: string): Promise<Market[]> {
  const seen = new Set<string>();
  const candidates: Market[] = [];
  const eventTitleBySlug = new Map<string, string>();

  function ingest(event: GammaEvent) {
    if (event.slug && !eventTitleBySlug.has(event.slug)) {
      eventTitleBySlug.set(event.slug, event.title ?? "");
    }
    for (const m of (event.markets ?? []) as Record<string, unknown>[]) {
      const mid = String(m.id);
      if (seen.has(mid) || m.closed || !m.enableOrderBook) continue;
      let outcomes: string[], prices: number[];
      try {
        outcomes = JSON.parse((m.outcomes as string) ?? "[]");
        prices = (JSON.parse((m.outcomePrices as string) ?? "[]") as string[])
          .map(Number);
      } catch {
        continue;
      }
      if (outcomes.length < 2 || outcomes.length !== prices.length) continue;
      if (isSettled(prices)) continue;
      if (isNoveltyMarket(event.title ?? "", (m.question as string) ?? "")) {
        continue;
      }
      seen.add(mid);
      candidates.push({
        id: mid,
        event: event.title ?? "",
        question: (m.question as string) ?? "",
        outcomes,
        prices,
        bestBid: toFloat(m.bestBid),
        bestAsk: toFloat(m.bestAsk),
      });
    }
  }

  let queries = await expandQueries(message);
  if (!queries.includes(message.trim())) queries = [...queries, message.trim()];

  const perQueryEvents = await Promise.all(queries.map(fetchEvents));
  perQueryEvents.forEach((events) => events.forEach(ingest));

  if (PROP_HINT.test(message) || !candidates.length) {
    // Rank candidate event slugs by title relevance to the question — not by
    // discovery order — so an off-topic query (an election, a weather event)
    // that happened to surface first can't starve the actual match's slug
    // out of the capped set of "more markets" lookups.
    const qTokens = tokens(message);
    const slugs = Array.from(eventTitleBySlug.entries())
      .map(([slug, title]) => {
        const tTokens = tokens(title);
        let score = 0;
        for (const w of qTokens) if (tTokens.has(w)) score++;
        return { slug, score };
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .map((x) => x.slug);
    const more = await Promise.all(slugs.map(fetchMoreMarketsEvent));
    more.forEach((event) => event && ingest(event));
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
      `[${i}] event: ${c.event} | market: ${c.question} | outcomes: ${
        JSON.stringify(c.outcomes)
      } | p0: ${c.prices[0]}`
    )
    .join("\n");
  const system =
    "You match a sports/betting question to the correct Polymarket market from the list. " +
    "Markets span match winners, totals (over/under), awards (MVP, Golden Boot, top " +
    "scorer, most assists), player-goal milestones and head-to-heads. Most are Yes/No. " +
    "Each market lists p0 = the probability of its FIRST outcome. " +
    "Notation: in 'A vs B 1' the 1 means the first team (A) to win, 2 the second team (B), " +
    "X a draw. Reply ONLY with compact JSON: " +
    '{"index": <int>, "outcome": "<exact outcome string the user is asking about>"}. ' +
    "Rules: (a) if the question names a specific competitor/pick, choose the market matching " +
    'it; for a Yes/No prop the outcome is usually "Yes". (b) if the question is a generic ' +
    "outright with NO named competitor (e.g. 'who wins the World Series', 'NBA MVP'), choose " +
    "the single market with the highest p0 — the favourite. (c) if nothing fits, use index -1.";
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
    `${outcomes[oi]} ${Math.round(p * 100)}c / ${outcomes[otherI]} ${
      Math.round(pOther * 100)
    }c`;
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
        return json({
          error: "Ask about an event, e.g. 'England vs Mexico 1'.",
        }, 400);
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
