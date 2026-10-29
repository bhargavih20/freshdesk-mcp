# Freshdesk MCP Server

Connects Claude to Freshdesk (API v2): tickets, conversations, contacts, companies, agents, groups.

## Tools
Read: list_tickets, search_tickets, get_ticket, list_conversations, search_contacts, get_contact,
find_company, get_company, list_agents, list_groups, list_ticket_fields
Write: create_ticket, update_ticket, reply_to_ticket, add_note  (disabled when READ_ONLY=true)

## 1. Get credentials
Freshdesk → profile icon → Profile settings → "View API key".
Domain is your helpdesk URL, e.g. `equalidentity.freshdesk.com`.

## 2a. Use in claude.ai (remote connector)
claude.ai connectors require a public HTTPS URL, so deploy the server first.

Deploy (Render / Railway / Fly.io / any Docker host) using the Dockerfile, with env vars:
FRESHDESK_DOMAIN, FRESHDESK_API_KEY, MCP_SECRET_PATH (long random string), optionally READ_ONLY=true.

Then in claude.ai: Settings → Connectors → Add custom connector
URL: `https://<your-host>/mcp/<MCP_SECRET_PATH>`

Security: the server uses your API key for every request, so anyone with the URL has your
Freshdesk access. Keep MCP_SECRET_PATH secret, consider READ_ONLY=true initially, and/or put
the service behind your organisation's network or an auth proxy. On Team/Enterprise plans,
an org Owner may need to add the connector.

## 2b. Use in Claude Desktop (local, no deployment)
```
pip install -r requirements.txt
```
Add to claude_desktop_config.json:
```json
{
  "mcpServers": {
    "freshdesk": {
      "command": "python",
      "args": ["/full/path/to/server.py"],
      "env": {
        "FRESHDESK_DOMAIN": "equalidentity.freshdesk.com",
        "FRESHDESK_API_KEY": "your_key",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```
Restart Claude Desktop.

## Reference codes
Status: 2 Open, 3 Pending, 4 Resolved, 5 Closed
Priority: 1 Low, 2 Medium, 3 High, 4 Urgent

## Notes
- list_tickets returns only the last 30 days unless updated_since is passed.
- search_tickets: 30 results/page, max 10 pages. Custom fields use the `cf_` prefix.
- Freshdesk API rate limits depend on plan; 429 errors report the retry window.
