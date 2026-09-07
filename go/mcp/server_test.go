package mcp

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func newTestServer() *Server {
	s := NewServer("test-server", "9.9.9")
	s.Instructions = "Be careful."
	s.Tool("add", "Add two integers.",
		ObjectSchema(map[string]Property{
			"a": {Type: "integer", Description: "First addend."},
			"b": {Type: "integer", Description: "Second addend."},
		}, []string{"a", "b"}),
		func(args map[string]any) (any, error) {
			return args["a"].(float64) + args["b"].(float64), nil
		})
	s.Tool("refuse", "Always fails.", nil, func(map[string]any) (any, error) {
		return nil, errors.New("order is archived")
	})
	return s
}

func call(t *testing.T, s *Server, body string) map[string]any {
	t.Helper()
	raw := s.Handle([]byte(body))
	if raw == nil {
		t.Fatalf("expected a reply for %s", body)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("reply is not JSON: %v", err)
	}
	return decoded
}

func TestInitializeEchoesSupportedVersion(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}`)
	result := reply["result"].(map[string]any)
	if got := result["protocolVersion"]; got != "2024-11-05" {
		t.Fatalf("protocolVersion = %v", got)
	}
	if result["instructions"] != "Be careful." {
		t.Fatalf("instructions missing")
	}
}

func TestInitializeFallsBackForUnknownVersion(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"1999-01-01"}}`)
	result := reply["result"].(map[string]any)
	if result["protocolVersion"] != DefaultProtocolVersion {
		t.Fatalf("expected fallback, got %v", result["protocolVersion"])
	}
}

func TestToolsListPreservesRegistrationOrder(t *testing.T) {
	reply := call(t, newTestServer(), `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	tools := reply["result"].(map[string]any)["tools"].([]any)
	if len(tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(tools))
	}
	if tools[0].(map[string]any)["name"] != "add" {
		t.Fatalf("order not preserved")
	}
}

func TestToolsCallReturnsContent(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}`)
	result := reply["result"].(map[string]any)
	if result["isError"] != false {
		t.Fatalf("unexpected error result")
	}
	text := result["content"].([]any)[0].(map[string]any)["text"].(string)
	if text != "5" {
		t.Fatalf("text = %q", text)
	}
}

func TestMissingArgumentIsInvalidParams(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"add","arguments":{"a":2}}}`)
	rpcErr := reply["error"].(map[string]any)
	if int(rpcErr["code"].(float64)) != InvalidParams {
		t.Fatalf("code = %v", rpcErr["code"])
	}
	if !strings.Contains(rpcErr["message"].(string), `"b"`) {
		t.Fatalf("message = %v", rpcErr["message"])
	}
}

func TestUnexpectedArgumentIsRejected(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"add","arguments":{"a":1,"b":2,"c":3}}}`)
	if reply["error"] == nil {
		t.Fatalf("expected an error")
	}
}

func TestHandlerErrorBecomesIsErrorNotTransportError(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"refuse"}}`)
	if reply["error"] != nil {
		t.Fatalf("tool failure must not become a JSON-RPC error")
	}
	result := reply["result"].(map[string]any)
	if result["isError"] != true {
		t.Fatalf("isError = %v", result["isError"])
	}
}

func TestUnknownToolAndMethod(t *testing.T) {
	reply := call(t, newTestServer(),
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"nope"}}`)
	if int(reply["error"].(map[string]any)["code"].(float64)) != MethodNotFound {
		t.Fatalf("expected MethodNotFound for unknown tool")
	}
	reply = call(t, newTestServer(), `{"jsonrpc":"2.0","id":1,"method":"does/not/exist"}`)
	if int(reply["error"].(map[string]any)["code"].(float64)) != MethodNotFound {
		t.Fatalf("expected MethodNotFound for unknown method")
	}
}

func TestNotificationsGetNoReply(t *testing.T) {
	if got := NewServer("t", "1").Handle([]byte(`{"jsonrpc":"2.0","method":"notifications/initialized"}`)); got != nil {
		t.Fatalf("notification produced a reply: %s", got)
	}
}

func TestServeStreamsResponses(t *testing.T) {
	in := strings.NewReader(
		`{"jsonrpc":"2.0","id":1,"method":"ping"}` + "\n" +
			`{"jsonrpc":"2.0","method":"notifications/initialized"}` + "\n" +
			`{"jsonrpc":"2.0","id":2,"method":"tools/list"}` + "\n")
	var out strings.Builder
	if err := newTestServer().Serve(in, &out); err != nil {
		t.Fatalf("Serve: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 replies, got %d: %q", len(lines), out.String())
	}
}

func TestIntegerValidationRejectsFractions(t *testing.T) {
	schema := ObjectSchema(map[string]Property{"n": {Type: "integer"}}, []string{"n"})
	if problems := ValidateArguments(schema, map[string]any{"n": 1.5}); len(problems) == 0 {
		t.Fatalf("1.5 should not validate as an integer")
	}
	if problems := ValidateArguments(schema, map[string]any{"n": 2.0}); len(problems) != 0 {
		t.Fatalf("2.0 should validate as an integer: %v", problems)
	}
}

func TestToContentPassesThroughReadyBlocks(t *testing.T) {
	blocks := []map[string]any{{"type": "text", "text": "hi"}}
	if got := ToContent(blocks); len(got) != 1 || got[0]["text"] != "hi" {
		t.Fatalf("blocks were re-encoded: %v", got)
	}
}
