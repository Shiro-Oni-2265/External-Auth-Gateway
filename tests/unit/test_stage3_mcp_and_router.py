"""Tests for Stage 3 Phase 3: MCP Client Session Lifecycle, tools/list, and Protocol Routing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.workspace.mcp_client import MCPStreamableClient, MCPError
from app.workspace.protocol_router import ProtocolRouter, ProtocolChoice
from app.workspace.circuit_breaker import AccountServiceCircuitBreaker
from app.workspace.adapters import encode_cursor, decode_cursor, ProtocolCursorMismatchError


# ============================================================================
# 1. MCP Client Session Lifecycle & tools/list
# ============================================================================

@pytest.mark.asyncio
async def test_mcp_client_tools_list_lifecycle():
    """MCPStreamableClient must support tools/list method adhering to JSON-RPC 2.0,
    passing user_id and connection_version in session headers.
    """
    client = MCPStreamableClient("calendar")
    
    mock_tools_resp = httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "list_events",
                        "description": "List calendar events",
                        "inputSchema": {"type": "object", "properties": {"calendarId": {"type": "string"}}},
                    }
                ]
            },
        },
        request=httpx.Request("POST", "https://calendarmcp.googleapis.com/mcp/v1"),
    )
    
    with patch.object(client._http_client, "send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_tools_resp
        
        tools = await client.list_tools(token="test_tok", user_id=1, connection_version=2)
        assert len(tools) == 1
        assert tools[0]["name"] == "list_events"
        
        # Verify request payload and session headers
        sent_req = mock_send.call_args[0][0]
        assert sent_req.headers["X-User-ID"] == "1"
        assert sent_req.headers["X-Connection-Version"] == "2"
        assert sent_req.headers["Authorization"] == "Bearer test_tok"
        
    await client.close()


@pytest.mark.asyncio
async def test_mcp_client_sse_streaming_response():
    """Verify MCPStreamableClient properly parses SSE (text/event-stream) messages."""
    client = MCPStreamableClient("docs")
    
    sse_body = "event: message\ndata: {\"jsonrpc\": \"2.0\", \"id\": 2, \"result\": {\"structuredContent\": {\"title\": \"Report\"}}}\n\n"
    mock_resp = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=sse_body,
        request=httpx.Request("POST", "https://docsmcp.googleapis.com/mcp/v1"),
    )
    
    with patch.object(client._http_client, "send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_resp
        
        res = await client.call_tool(
            token="tok",
            user_id=1,
            connection_version=1,
            name="read_doc",
            arguments={"documentId": "doc123"},
        )
        assert res == {"title": "Report"}
        
    await client.close()


# ============================================================================
# 2. Protocol Router Fallback & Cursor Integrity (No Cross-Protocol Leaks)
# ============================================================================

def test_protocol_router_falls_back_to_rest_when_mcp_unsupported():
    """Operations without verified MCP schema mapping or operations with media
    must route to REST fallback.
    """
    router = ProtocolRouter(mcp_enabled=True)
    
    # 1. Supported read -> MCP
    choice = router.select_protocol("calendar", "events.list", {"calendarId": "primary"}, {})
    assert choice == ProtocolChoice.MCP
    
    # 2. Media present -> REST
    choice_media = router.select_protocol("drive", "files.create", {}, {"name": "test.pdf"}, media={"bytes": "..."})
    assert choice_media == ProtocolChoice.REST
    
    # 3. Unmapped operation -> REST
    choice_unmapped = router.select_protocol("gmail", "unknown.custom.op", {}, {})
    assert choice_unmapped == ProtocolChoice.REST


def test_protocol_router_falls_back_when_circuit_breaker_tripped():
    """If circuit breaker is tripped for this (user_id, service), fallback to REST."""
    cb = AccountServiceCircuitBreaker(failure_threshold=1)
    cb.record_failure(user_id=1, service="calendar")
    
    router = ProtocolRouter(circuit_breaker=cb, mcp_enabled=True)
    choice = router.select_protocol("calendar", "events.list", {"calendarId": "primary"}, {}, user_id=1)
    assert choice == ProtocolChoice.REST
    
    # Other user still gets MCP if available
    choice_other = router.select_protocol("calendar", "events.list", {"calendarId": "primary"}, {}, user_id=2)
    assert choice_other == ProtocolChoice.MCP


def test_cursor_protocol_integrity_cannot_mix():
    """Pagination cursors must encapsulate protocol and prevent mixing MCP with REST tokens."""
    mcp_cursor = encode_cursor("mcp", "page_token_abc")
    assert isinstance(mcp_cursor, str)
    
    # Valid decode
    proto, token = decode_cursor(mcp_cursor, expected_protocol="mcp")
    assert proto == "mcp"
    assert token == "page_token_abc"
    
    # Attempting to decode MCP cursor expecting REST must raise ProtocolCursorMismatchError
    with pytest.raises(ProtocolCursorMismatchError):
        decode_cursor(mcp_cursor, expected_protocol="rest")
