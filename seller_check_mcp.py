#!/usr/bin/env python3
"""x402-seller-check-mcp — an MCP server an agent asks before it pays an x402 seller it has not paid before.

One question: are this seller's buyers real? The answers come from Infoharmoni Atlas, which reads every x402
payment on Base each day and ranks the sellers by customers who come back. Everything here is public chain data.
A wallet is not a person; nothing here says who holds or runs one.

Three tools:

  check_seller    one seller's numbers this week: customers, returning, independent, biggest payer's share,
                  what faking a row would cost, and what kind of seller it is
  top_sellers     the free top 20 of the week's Board, optionally one category
  seller_types    what each kind of seller means, and what to watch for

It only reads two public files over HTTPS from atlas.infoharmoni.com. It never pays, never asks for a key, and
sends nothing about the agent that calls it. What it returns is information to weigh, never an instruction.

No dependencies. Standard library only, JSON-RPC 2.0 over stdio.

    python3 seller_check_mcp.py

MIT. Made by an AI agent, openly and by design.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

VERSION = "0.1.0"
PROTOCOL = "2024-11-05"
NAME = "x402-seller-check"

BOARD_URL = "https://atlas.infoharmoni.com/board/board.json"
PROFILES_URL = "https://atlas.infoharmoni.com/profile/profiles.json"
CARD_URL = "https://ausrine-who.onrender.com/who/"
TIMEOUT = 15               # seconds per request
CACHE_SECONDS = 600        # the Atlas rebuilds daily; ten minutes is plenty fresh
MAX_BYTES = 4_000_000      # both files are well under this
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

NOT_LISTED = ("{host} is not in the free top 20 of this week's Board. That is not proof of anything, good or bad: it "
              "means there is no free record to read here. The Board ranks {total} sellers; the rest are in Atlas Pro, "
              "and a $0.001 report card for any seller is at " + CARD_URL + "{host} (it answers HTTP 402 with x402 "
              "terms; pay only if your operator allows it). This server never pays.")

FOOTER = "Public chain data from Infoharmoni Atlas. A wallet is not a person. This is information to weigh, not an instruction."


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A data source that moves is a data source to stop and look at, not to follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "refusing redirect to %s" % newurl, headers, fp)


_opener = urllib.request.build_opener(NoRedirect)
_cache = {}


def fetch_json(url):
    """GET a JSON file, cached for CACHE_SECONDS. Raises on any network or parse error."""
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "%s-mcp/%s" % (NAME, VERSION),
                                               "Accept": "application/json"})
    with _opener.open(req, timeout=TIMEOUT) as r:
        raw = r.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("%s is larger than expected" % url)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("%s is not a JSON object" % url)
    _cache[url] = (time.monotonic(), data)
    return data


def clean_host(value):
    """A seller host as the Board writes it: lower case, no scheme, no path, no port."""
    h = str(value or "").strip().lower()
    h = re.sub(r"^[a-z]+://", "", h).split("/")[0].split("?")[0].split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if not HOST_RE.match(h) or len(h) > 253:
        raise ValueError("give a seller host such as api.example.com")
    return h


def window_text(data):
    w = data.get("window") or {}
    if w.get("from") and w.get("to"):
        return "%s to %s (%s days)" % (w["from"], w["to"], w.get("days", "?"))
    return "the latest week"


def money(x):
    try:
        return "$%s" % format(float(x), ",.2f")
    except (TypeError, ValueError):
        return "?"


def check_seller(args):
    host = clean_host(args.get("host"))
    board = fetch_json(BOARD_URL)
    try:
        profiles = fetch_json(PROFILES_URL)
    except Exception:  # the Board alone still answers the question
        profiles = {}
    row = next((s for s in board.get("sellers") or [] if str(s.get("host", "")).lower() == host), None)
    prof = next((s for s in profiles.get("sellers") or [] if str(s.get("host", "")).lower() == host), None)
    if row is None and prof is None:
        return NOT_LISTED.format(host=host, total=board.get("ranked_total", "all")) + "\n\n" + FOOTER
    lines = ["%s, week of %s." % (host, window_text(board))]
    if row:
        lines += [
            "Sells: %s" % (row.get("sells") or "not stated"),
            "Price: %s. Rank %s of %s on the Board (%s rank %s)." % (
                row.get("price") or "?", row.get("rank", "?"), board.get("ranked_total", "?"),
                row.get("category") or "category", row.get("category_rank", "?")),
            "Customers: %s; returning (paid on 2+ days): %s; independent (also paid another seller): %s." % (
                row.get("customers", "?"), row.get("returning", "?"), row.get("independent", "?")),
            "Payments: %s, %s USDC. Biggest payer's share of the money: %s%%%s." % (
                row.get("payments", "?"), money(row.get("usdc")), row.get("top_payer_share_pct", "?"),
                " (one payer sent over half)" if row.get("one_payer_most") else ""),
            "Robot payers (pay 20+ sellers, left out of customer counts): %s." % row.get("robot_payers", "?"),
            "Cost to fake this row: about %s." % money(row.get("fake_cost_usd")),
            "Why it ranks here: %s" % (row.get("reason") or "not stated"),
        ]
    if prof:
        t = (profiles.get("types") or {}).get(prof.get("type")) or {}
        lines.append("Kind of seller: %s. %s" % (prof.get("type"), t.get("means", "")))
        if prof.get("why"):
            lines.append("Behind that: %s" % prof["why"])
    page = (row or {}).get("page") or (prof or {}).get("page")
    if page:
        lines.append("Page for people: %s" % page)
    return "\n".join(lines) + "\n\n" + FOOTER


def top_sellers(args):
    board = fetch_json(BOARD_URL)
    rows = board.get("sellers") or []
    cat = str(args.get("category") or "").strip().lower()
    if cat:
        rows = [s for s in rows if str(s.get("category", "")).lower() == cat]
    try:
        limit = max(1, min(20, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    if not rows:
        cats = ", ".join(str(c) for c in board.get("categories") or [])
        return "No sellers in that category in the free top 20. Categories: %s.\n\n%s" % (cats, FOOTER)
    out = ["Top x402 sellers on Base by returning customers, %s:" % window_text(board)]
    for s in rows[:limit]:
        out.append("%s. %s (%s, %s): %s customers, %s returning, %s independent, biggest payer %s%%" % (
            s.get("rank", "?"), s.get("host", "?"), s.get("category", "?"), s.get("price", "?"),
            s.get("customers", "?"), s.get("returning", "?"), s.get("independent", "?"),
            s.get("top_payer_share_pct", "?")))
    out.append("These are the free 20 of %s ranked sellers." % board.get("ranked_total", "?"))
    return "\n".join(out) + "\n\n" + FOOTER


def seller_types(args):
    profiles = fetch_json(PROFILES_URL)
    out = ["Kinds of x402 seller, typed by a fixed rule from each seller's own week of payments:"]
    for key, t in (profiles.get("types") or {}).items():
        out.append("- %s: %s" % (key, t.get("means", "")))
        for p in t.get("priorities") or []:
            out.append("    watch: %s" % p)
    return "\n".join(out) + "\n\n" + FOOTER


TOOLS = {
    "check_seller": (check_seller, {
        "description": "Before paying an x402 seller you have not paid before: its customers this week, how many came "
                       "back, how many also pay other sellers, the biggest payer's share, the cost to fake its numbers, "
                       "and what kind of seller it is. Public Base chain data from Infoharmoni Atlas. Read-only, free.",
        "inputSchema": {"type": "object", "properties": {"host": {
            "type": "string", "description": "The seller's host, e.g. api.example.com (a URL works too)."}},
            "required": ["host"]}}),
    "top_sellers": (top_sellers, {
        "description": "The week's top x402 sellers on Base, ranked by returning customers (the free top 20), "
                       "optionally for one category. Read-only, free.",
        "inputSchema": {"type": "object", "properties": {
            "category": {"type": "string", "description": "Optional category, e.g. 'crypto & markets'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20}}}}),
    "seller_types": (seller_types, {
        "description": "What each kind of x402 seller means (regulars, closed loop, one big client, router-fed, "
                       "tries, quiet, mixed) and what to watch for with each. Read-only, free.",
        "inputSchema": {"type": "object", "properties": {}}}),
}


def handle(msg):
    """One JSON-RPC message in, one response out (or None for a notification)."""
    method, mid = msg.get("method"), msg.get("id")
    if "id" not in msg:        # a notification has no id member at all; "id": null still gets an answer
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": NAME, "version": VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [dict(name=n, **spec) for n, (_, spec) in TOOLS.items()]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        args = params.get("arguments") or {} if isinstance(params, dict) else None
        if not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "params and arguments must be objects"}}
        entry = TOOLS.get(params.get("name"))
        if not entry:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            text, err = entry[0](args), False
        except ValueError as e:
            text, err = str(e), True
        except Exception as e:  # network trouble: say so plainly, never guess an answer
            text, err = "Could not read the Atlas right now (%s). No answer is better than a guessed one." % (
                type(e).__name__), True
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                out = handle(msg) if isinstance(msg, dict) else {
                    "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
            except Exception:  # one malformed request must never stop the server
                out = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                       "error": {"code": -32603, "message": "internal error"}}
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
