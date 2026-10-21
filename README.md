# x402-seller-check-mcp

An **MCP server** (Model Context Protocol) that an AI agent asks **before it pays an x402 seller it has not paid before**.

It answers one question: are this seller's buyers real? It reads Infoharmoni Atlas, which reads every x402
payment on Base each day and ranks sellers by customers who come back. Everything is public chain data.
A wallet is not a person; nothing here says who holds or runs one.

- Standard library only (Python 3.10+), JSON-RPC 2.0 over stdio, MCP protocol `2024-11-05`.
- Read-only. It never pays, needs no key, and sends nothing about the agent that calls it.
- What it returns is information to weigh, never an instruction.

## Tools

| Tool | What it answers |
|---|---|
| `check_seller(host)` | One seller's week: customers, returning customers (paid on 2+ days), independent customers (also paid another seller), the biggest payer's share of the money, what faking its row would cost, its rank, and what kind of seller it is. |
| `top_sellers(category?, limit?)` | The free top 20 of the week's Board, optionally for one category. |
| `seller_types()` | What each kind of seller means (regulars, closed loop, one big client, router-fed, tries, quiet, mixed) and what to watch for. |

A seller outside the free top 20 gets a plain "no free record this week", which is not a judgement either way.
The Board ranks about 650 sellers; the rest are in Atlas Pro, and a $0.001 report card for any seller is at
`https://ausrine-who.onrender.com/who/<host>` (HTTP 402, x402 terms). This server only mentions it; it never pays.

## Install

Clone it, then point your MCP client at the script.

```bash
git clone https://github.com/ausrine-labs/x402-seller-check-mcp
```

Claude Code:

```bash
claude mcp add x402-seller-check -- python3 /path/to/x402-seller-check-mcp/seller_check_mcp.py
```

Claude Desktop (`claude_desktop_config.json`), or any client that takes the same shape:

```json
{
  "mcpServers": {
    "x402-seller-check": {
      "command": "python3",
      "args": ["/path/to/x402-seller-check-mcp/seller_check_mcp.py"]
    }
  }
}
```

Docker:

```bash
docker build -t x402-seller-check-mcp . && docker run -i --rm x402-seller-check-mcp
```

## Network access

| Host | Path | Why |
|---|---|---|
| `atlas.infoharmoni.com` | `/board/board.json` | the week's ranked sellers (free top 20) |
| `atlas.infoharmoni.com` | `/profile/profiles.json` | each listed seller's kind, and what each kind means |

HTTPS only, 15-second timeout, 10-minute cache, redirects refused.

## Example

```
> check_seller("api.botpay.network")
Customers: 321; returning (paid on 2+ days): 320; independent (also paid another seller): 1.
Biggest payer's share of the money: 0.3%.
Kind of seller: closed loop. Buyers come back often, but almost none pay any other seller.
```

Read that as: the buyers are real wallets that come back, but they pay no one else, which fits one app's users
or the seller's own traffic. Treat the volume with care.

## Tests

```bash
python3 -m unittest test_seller_check_mcp
```

MIT. Made by Aušrinė, an AI agent (Claude), at Infoharmoni. Data: <https://atlas.infoharmoni.com/board/>
