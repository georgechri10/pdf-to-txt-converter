"""SharpOdds API — find a single event on Polymarket and return just the odds.

Design:
  * A cheap DeepSeek model is used ONLY for the fuzzy natural-language match
    (turning "England vs Ghana 1" into the correct Polymarket market/outcome).
  * All odds numbers are computed deterministically in Python from Polymarket's
    own prices, so they are never hallucinated by the model.
"""

import os
import re
import json
import urllib.request
import urllib.parse

from flask import Flask, request, jsonify

app = Flask(__name__)

GAMMA_SEARCH = "https://gamma-api.polymarket.com/public-search"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
UA = "SharpOdds/1.0"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url, payload, headers, timeout=25):
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": UA}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Polymarket
# --------------------------------------------------------------------------- #
def _clean_query(message):
    """Drop 1X2 shorthand tokens so the text search matches team names."""
    tokens = [t for t in re.split(r"\s+", message.strip()) if t]
    kept = [t for t in tokens if t.lower() not in {"1", "2", "x", "vs", "v", "vs."}]
    return " ".join(kept) if kept else message.strip()


def search_markets(message):
    """Return a flat list of tradeable candidate markets from Polymarket."""
    seen = set()
    candidates = []

    def collect(query):
        if not query:
            return
        url = GAMMA_SEARCH + "?" + urllib.parse.urlencode(
            {"q": query, "limit_per_type": 8, "events_status": "active"}
        )
        try:
            data = _get_json(url)
        except Exception:
            return
        for event in data.get("events", []):
            for m in event.get("markets", []):
                mid = m.get("id")
                if mid in seen or m.get("closed") or not m.get("enableOrderBook"):
                    continue
                try:
                    outcomes = json.loads(m.get("outcomes") or "[]")
                    prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
                except (ValueError, TypeError):
                    continue
                if len(outcomes) < 2 or len(outcomes) != len(prices):
                    continue
                seen.add(mid)
                candidates.append({
                    "id": mid,
                    "event": event.get("title", ""),
                    "question": m.get("question", ""),
                    "outcomes": outcomes,
                    "prices": prices,
                    "bestBid": _to_float(m.get("bestBid")),
                    "bestAsk": _to_float(m.get("bestAsk")),
                })

    collect(_clean_query(message))
    if not candidates:
        collect(message.strip())
    return candidates[:25]


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# DeepSeek match (with a deterministic fallback)
# --------------------------------------------------------------------------- #
def _fallback_pick(message, candidates):
    """Token-overlap scoring used if DeepSeek is unavailable."""
    words = set(re.findall(r"[a-z0-9]+", message.lower()))
    best, best_score = None, -1
    for i, c in enumerate(candidates):
        text = (c["question"] + " " + c["event"] + " " + " ".join(c["outcomes"])).lower()
        cwords = set(re.findall(r"[a-z0-9]+", text))
        score = len(words & cwords)
        if score > best_score:
            best, best_score = i, score
    return {"index": best or 0, "outcome": candidates[best or 0]["outcomes"][0]}


def deepseek_pick(message, candidates):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return _fallback_pick(message, candidates)

    listing = "\n".join(
        f"[{i}] event: {c['event']} | market: {c['question']} | outcomes: {c['outcomes']}"
        for i, c in enumerate(candidates)
    )
    system = (
        "You match a sports/betting question to the correct Polymarket market. "
        "Notation: in 'A vs B 1' the 1 means the first team (A) to win, 2 the second "
        "team (B), X a draw. Reply ONLY with compact JSON: "
        '{\"index\": <int>, \"outcome\": \"<exact outcome string the user is asking about>\"}. '
        "Pick the single market that best answers the question. If none fit, use index -1."
    )
    user = f"Question: {message}\n\nMarkets:\n{listing}"
    try:
        resp = _post_json(
            DEEPSEEK_URL,
            {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "max_tokens": 60,
            },
            {"Authorization": f"Bearer {key}"},
        )
        content = resp["choices"][0]["message"]["content"]
        pick = json.loads(content)
        idx = int(pick.get("index", -1))
        if idx < 0 or idx >= len(candidates):
            return None
        outcome = pick.get("outcome") or candidates[idx]["outcomes"][0]
        return {"index": idx, "outcome": outcome}
    except Exception:
        return _fallback_pick(message, candidates)


# --------------------------------------------------------------------------- #
# Odds formatting (deterministic)
# --------------------------------------------------------------------------- #
def _decimal(p):
    return round(1.0 / p, 2) if p and p > 0 else None


def format_odds(market, outcome):
    outcomes = market["outcomes"]
    prices = market["prices"]

    # Match the requested outcome (case-insensitive), default to first.
    oi = 0
    for i, o in enumerate(outcomes):
        if o.lower() == str(outcome).lower():
            oi = i
            break

    p = prices[oi]
    other_i = 1 if oi == 0 else 0
    p_other = prices[other_i]

    # Best bid/ask are quoted on outcome 0. Flip for the second outcome.
    bid, ask = market["bestBid"], market["bestAsk"]
    if oi == 0:
        o_bid, o_ask = bid, ask
    elif bid is not None and ask is not None:
        o_bid, o_ask = 1 - ask, 1 - bid
    else:
        o_bid, o_ask = None, None

    parts = [
        market["question"] or market["event"],
        "—",
        f"{outcomes[oi]} {round(p * 100)}c / {outcomes[other_i]} {round(p_other * 100)}c",
    ]
    back = _decimal(o_ask)  # you buy at the ask
    lay = _decimal(o_bid)   # you sell at the bid
    if back and lay:
        parts.append(f"· back {back:.2f} lay {lay:.2f}")

    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Route
# --------------------------------------------------------------------------- #
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/ask", methods=["POST", "OPTIONS"])
def ask():
    if request.method == "OPTIONS":
        return _cors(jsonify({}))

    try:
        data = request.get_json(force=True) or {}
        message = (data.get("message") or "").strip()
        if not message:
            return _cors(jsonify({"error": "Ask about an event, e.g. 'England vs Mexico 1'."})), 400

        candidates = search_markets(message)
        if not candidates:
            return _cors(jsonify({"answer": "No market found."}))

        pick = deepseek_pick(message, candidates)
        if not pick:
            return _cors(jsonify({"answer": "No market found."}))

        market = candidates[pick["index"]]
        answer = format_odds(market, pick["outcome"])
        return _cors(jsonify({"answer": answer}))

    except Exception as e:
        return _cors(jsonify({"error": f"Server error: {e}"})), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
