"""
Freshdesk MCP Server
Exposes Freshdesk (API v2) tickets, conversations, contacts, companies,
agents and groups as MCP tools.

Env vars:
  FRESHDESK_DOMAIN     e.g. "equalidentity" or "equalidentity.freshdesk.com"
  FRESHDESK_API_KEY    Freshdesk agent API key (Profile settings > API key)
  READ_ONLY            "true" to disable all write tools (default: false)
  MCP_TRANSPORT        "stdio" (Claude Desktop) or "http" (remote / claude.ai). Default: http
  PORT                 HTTP port (default 8000)
  MCP_SECRET_PATH      Optional secret appended to the endpoint path, e.g. /mcp/<secret>
"""

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# ---------------------------------------------------------------- config
_domain = os.environ.get("FRESHDESK_DOMAIN", "").strip().replace("https://", "").rstrip("/")
if _domain and "." not in _domain:
    _domain = f"{_domain}.freshdesk.com"
API_KEY = os.environ.get("FRESHDESK_API_KEY", "")
BASE_URL = f"https://{_domain}/api/v2"
READ_ONLY = os.environ.get("READ_ONLY", "false").lower() == "true"
TRANSPORT = os.environ.get("MCP_TRANSPORT", "http").lower()
SECRET = os.environ.get("MCP_SECRET_PATH", "").strip("/")

STATUS = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
PRIORITY = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
SOURCE = {1: "Email", 2: "Portal", 3: "Phone", 7: "Chat", 9: "Feedback Widget", 10: "Outbound Email"}

mcp = FastMCP(
    "freshdesk",
    instructions=(
        "Freshdesk helpdesk tools. Status codes: 2=Open, 3=Pending, 4=Resolved, 5=Closed. "
        "Priority: 1=Low, 2=Medium, 3=High, 4=Urgent. Use search_tickets for filtered queries. "
        "Replies are sent to the customer; notes are internal unless private=false. "
        "Confirm with the user before sending replies or changing ticket fields."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path=f"/mcp/{SECRET}" if SECRET else "/mcp",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ---------------------------------------------------------------- helpers
async def _request(method: str, path: str, params: dict | None = None, json: dict | None = None) -> Any:
    if not _domain or not API_KEY:
        raise RuntimeError("FRESHDESK_DOMAIN and FRESHDESK_API_KEY must be set.")
    async with httpx.AsyncClient(auth=(API_KEY, "X"), timeout=30) as client:
        r = await client.request(method, BASE_URL + path, params=params, json=json)
    if r.status_code == 429:
        raise RuntimeError(f"Freshdesk rate limit hit. Retry after {r.headers.get('Retry-After', '?')}s.")
    if r.status_code >= 400:
        raise RuntimeError(f"Freshdesk API error {r.status_code}: {r.text[:800]}")
    return r.json() if r.content else {"ok": True}


def _guard_write():
    if READ_ONLY:
        raise RuntimeError("Server is in READ_ONLY mode; write operations are disabled.")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _ticket_summary(t: dict) -> dict:
    return _clean({
        "id": t.get("id"),
        "subject": t.get("subject"),
        "status": STATUS.get(t.get("status"), t.get("status")),
        "priority": PRIORITY.get(t.get("priority"), t.get("priority")),
        "source": SOURCE.get(t.get("source"), t.get("source")),
        "type": t.get("type"),
        "requester_id": t.get("requester_id"),
        "requester": (t.get("requester") or {}).get("email") if t.get("requester") else None,
        "company_id": t.get("company_id"),
        "responder_id": t.get("responder_id"),
        "group_id": t.get("group_id"),
        "tags": t.get("tags") or None,
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "due_by": t.get("due_by"),
        "fr_due_by": t.get("fr_due_by"),
        "custom_fields": {k: v for k, v in (t.get("custom_fields") or {}).items() if v is not None} or None,
    })


# ---------------------------------------------------------------- read tools
@mcp.tool()
async def list_tickets(
    filter: Optional[str] = None,
    email: Optional[str] = None,
    company_id: Optional[int] = None,
    updated_since: Optional[str] = None,
    order_by: str = "updated_at",
    order_type: str = "desc",
    page: int = 1,
    per_page: int = 30,
) -> list[dict]:
    """List tickets. Note: by default Freshdesk only returns tickets created in the last 30 days;
    pass updated_since (ISO date, e.g. 2026-01-01) to go further back.
    filter: new_and_my_open | watching | spam | deleted.
    order_by: created_at | due_by | updated_at | status. per_page max 100."""
    params = _clean({
        "filter": filter, "email": email, "company_id": company_id, "updated_since": updated_since,
        "order_by": order_by, "order_type": order_type, "page": page,
        "per_page": min(per_page, 100), "include": "requester",
    })
    data = await _request("GET", "/tickets", params=params)
    return [_ticket_summary(t) for t in data]


@mcp.tool()
async def search_tickets(query: str, page: int = 1) -> dict:
    """Search tickets with Freshdesk query syntax (do NOT wrap in quotes; added automatically).
    Examples: "status:2 AND priority:4", "tag:'escalation'", "company_id:123 AND status:3",
    "created_at:>'2026-10-01'", "cf_client_name:'JSW'" (custom fields use cf_ prefix).
    Returns 30 results per page, max 10 pages."""
    data = await _request("GET", "/search/tickets", params={"query": f'"{query}"', "page": page})
    return {"total": data.get("total"), "results": [_ticket_summary(t) for t in data.get("results", [])]}


@mcp.tool()
async def get_ticket(ticket_id: int, include_conversations: bool = True) -> dict:
    """Get full ticket details: description, requester, company, stats and (optionally) conversation thread."""
    include = "requester,company,stats" + (",conversations" if include_conversations else "")
    t = await _request("GET", f"/tickets/{ticket_id}", params={"include": include})
    out = _ticket_summary(t)
    out["description"] = t.get("description_text")
    out["requester"] = t.get("requester")
    out["company"] = t.get("company")
    out["stats"] = t.get("stats")
    if include_conversations:
        out["conversations"] = [
            _clean({
                "id": c.get("id"),
                "incoming": c.get("incoming"),
                "private": c.get("private"),
                "from": c.get("from_email"),
                "user_id": c.get("user_id"),
                "created_at": c.get("created_at"),
                "body": c.get("body_text"),
            })
            for c in t.get("conversations", [])
        ]
    return out


@mcp.tool()
async def list_conversations(ticket_id: int, page: int = 1) -> list[dict]:
    """List all replies and notes on a ticket (use when a thread exceeds the 10 returned by get_ticket)."""
    data = await _request("GET", f"/tickets/{ticket_id}/conversations", params={"page": page, "per_page": 100})
    return [
        _clean({"id": c["id"], "incoming": c.get("incoming"), "private": c.get("private"),
                "from": c.get("from_email"), "created_at": c.get("created_at"), "body": c.get("body_text")})
        for c in data
    ]


@mcp.tool()
async def search_contacts(query: str) -> dict:
    """Search contacts. Examples: "email:'a@b.com'", "company_id:123", "name:'Ravi'"."""
    data = await _request("GET", "/search/contacts", params={"query": f'"{query}"'})
    return data


@mcp.tool()
async def get_contact(contact_id: int) -> dict:
    """Get a contact by ID."""
    return await _request("GET", f"/contacts/{contact_id}")


@mcp.tool()
async def find_company(name: str) -> list[dict]:
    """Find companies by (partial) name. Returns id and name — use company_id in ticket searches."""
    data = await _request("GET", "/companies/autocomplete", params={"name": name})
    return data.get("companies", [])


@mcp.tool()
async def get_company(company_id: int) -> dict:
    """Get a company by ID."""
    return await _request("GET", f"/companies/{company_id}")


@mcp.tool()
async def list_agents(page: int = 1) -> list[dict]:
    """List agents (id, name, email) — use to resolve responder_id."""
    data = await _request("GET", "/agents", params={"page": page, "per_page": 100})
    return [{"id": a["id"], "name": a["contact"].get("name"), "email": a["contact"].get("email"),
             "available": a.get("available")} for a in data]


@mcp.tool()
async def list_groups() -> list[dict]:
    """List agent groups (id, name) — use to resolve group_id."""
    data = await _request("GET", "/groups", params={"per_page": 100})
    return [{"id": g["id"], "name": g.get("name")} for g in data]


@mcp.tool()
async def list_ticket_fields() -> list[dict]:
    """List ticket fields, including custom fields (cf_*) and their allowed choices."""
    data = await _request("GET", "/ticket_fields")
    return [_clean({"name": f.get("name"), "label": f.get("label"), "type": f.get("type"),
                    "choices": f.get("choices") or None}) for f in data]


# ---------------------------------------------------------------- write tools
@mcp.tool()
async def create_ticket(
    subject: str,
    description: str,
    email: str,
    priority: int = 2,
    status: int = 2,
    type: Optional[str] = None,
    group_id: Optional[int] = None,
    responder_id: Optional[int] = None,
    tags: Optional[list[str]] = None,
    cc_emails: Optional[list[str]] = None,
    custom_fields: Optional[dict] = None,
) -> dict:
    """Create a ticket. priority 1-4, status 2-5. description accepts HTML."""
    _guard_write()
    body = _clean({
        "subject": subject, "description": description, "email": email, "priority": priority,
        "status": status, "source": 2, "type": type, "group_id": group_id, "responder_id": responder_id,
        "tags": tags, "cc_emails": cc_emails, "custom_fields": custom_fields,
    })
    return _ticket_summary(await _request("POST", "/tickets", json=body))


@mcp.tool()
async def update_ticket(
    ticket_id: int,
    status: Optional[int] = None,
    priority: Optional[int] = None,
    responder_id: Optional[int] = None,
    group_id: Optional[int] = None,
    type: Optional[str] = None,
    tags: Optional[list[str]] = None,
    due_by: Optional[str] = None,
    custom_fields: Optional[dict] = None,
) -> dict:
    """Update ticket properties. Only provided fields change. Note: tags REPLACES existing tags."""
    _guard_write()
    body = _clean({
        "status": status, "priority": priority, "responder_id": responder_id, "group_id": group_id,
        "type": type, "tags": tags, "due_by": due_by, "custom_fields": custom_fields,
    })
    if not body:
        raise ValueError("No fields provided to update.")
    return _ticket_summary(await _request("PUT", f"/tickets/{ticket_id}", json=body))


@mcp.tool()
async def reply_to_ticket(ticket_id: int, body: str, cc_emails: Optional[list[str]] = None) -> dict:
    """Send a PUBLIC reply to the customer on a ticket (emails the requester). body accepts HTML."""
    _guard_write()
    r = await _request("POST", f"/tickets/{ticket_id}/reply", json=_clean({"body": body, "cc_emails": cc_emails}))
    return {"id": r.get("id"), "ticket_id": ticket_id, "created_at": r.get("created_at"), "sent": True}


@mcp.tool()
async def add_note(ticket_id: int, body: str, private: bool = True, notify_emails: Optional[list[str]] = None) -> dict:
    """Add a note to a ticket. private=True (default) keeps it internal. notify_emails: agent emails to notify."""
    _guard_write()
    r = await _request("POST", f"/tickets/{ticket_id}/notes",
                       json=_clean({"body": body, "private": private, "notify_emails": notify_emails}))
    return {"id": r.get("id"), "ticket_id": ticket_id, "private": private, "created_at": r.get("created_at")}


# ---------------------------------------------------------------- entrypoint
if __name__ == "__main__":
    mcp.run(transport="stdio" if TRANSPORT == "stdio" else "streamable-http")
