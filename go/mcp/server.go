// Package mcp is a dependency-free Model Context Protocol server for Go.
//
// It mirrors the Python package in this repository: register tools, serve
// JSON-RPC 2.0 over a newline-delimited stream, keep tool failures out of the
// transport layer.
//
//	s := mcp.NewServer("internal-tools", "0.1.0")
//	s.Tool("get_order", "Look up an order.", schema, func(args map[string]any) (any, error) {
//	    return lookup(args["order_id"].(string))
//	})
//	s.Serve(os.Stdin, os.Stdout)
package mcp

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"sync"
)

// Protocol revisions this implementation understands, newest first.
var SupportedProtocolVersions = []string{"2025-06-18", "2025-03-26", "2024-11-05"}

// DefaultProtocolVersion is advertised when the client asks for an unknown revision.
const DefaultProtocolVersion = "2025-06-18"

// JSON-RPC error codes.
const (
	ParseError     = -32700
	InvalidRequest = -32600
	MethodNotFound = -32601
	InvalidParams  = -32602
	InternalError  = -32603
	AccessDenied   = -32001
	RateLimited    = -32002
)

// Handler runs one tool call. Returning an error yields an isError result
// rather than a JSON-RPC error, matching the Python implementation.
type Handler func(arguments map[string]any) (any, error)

// Tool is a callable exposed to the agent.
type Tool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"inputSchema"`
	handler     Handler
}

// Server holds the tool registry and dispatches JSON-RPC messages.
type Server struct {
	Name         string
	Version      string
	Instructions string

	mu      sync.RWMutex
	tools   map[string]*Tool
	order   []string
	version string
}

// NewServer returns a server with no tools registered.
func NewServer(name, version string) *Server {
	return &Server{
		Name:    name,
		Version: version,
		tools:   make(map[string]*Tool),
		version: DefaultProtocolVersion,
	}
}

// Tool registers a callable. Re-registering a name replaces it.
func (s *Server) Tool(name, description string, schema map[string]any, handler Handler) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.tools[name]; !exists {
		s.order = append(s.order, name)
	}
	if schema == nil {
		schema = ObjectSchema(nil, nil)
	}
	s.tools[name] = &Tool{Name: name, Description: description, InputSchema: schema, handler: handler}
}

// Tools returns the registry in registration order.
func (s *Server) Tools() []*Tool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Tool, 0, len(s.order))
	for _, name := range s.order {
		out = append(out, s.tools[name])
	}
	return out
}

type request struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type response struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

// Serve reads newline-delimited JSON from in and writes replies to out until
// in reaches EOF. Notifications (messages without an id) get no reply.
func (s *Server) Serve(in io.Reader, out io.Writer) error {
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	writer := bufio.NewWriter(out)
	defer writer.Flush()

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		reply := s.Handle(line)
		if reply == nil {
			continue
		}
		if _, err := writer.Write(append(reply, '\n')); err != nil {
			return err
		}
		if err := writer.Flush(); err != nil {
			return err
		}
	}
	return scanner.Err()
}

// Handle processes one raw message and returns the encoded reply, or nil when
// the message was a notification or could not be parsed.
func (s *Server) Handle(raw []byte) []byte {
	var req request
	if err := json.Unmarshal(raw, &req); err != nil {
		return encode(response{JSONRPC: "2.0", ID: json.RawMessage("null"),
			Error: &rpcError{Code: ParseError, Message: "invalid JSON"}})
	}
	if req.JSONRPC != "2.0" || req.Method == "" {
		if req.ID == nil {
			return nil
		}
		return encode(response{JSONRPC: "2.0", ID: req.ID,
			Error: &rpcError{Code: InvalidRequest, Message: "not a JSON-RPC 2.0 request"}})
	}

	result, rpcErr := s.dispatch(req)
	if req.ID == nil {
		return nil
	}
	if rpcErr != nil {
		return encode(response{JSONRPC: "2.0", ID: req.ID, Error: rpcErr})
	}
	if result == nil {
		result = map[string]any{}
	}
	return encode(response{JSONRPC: "2.0", ID: req.ID, Result: result})
}

func (s *Server) dispatch(req request) (any, *rpcError) {
	switch req.Method {
	case "initialize":
		return s.initialize(req.Params), nil
	case "ping":
		return map[string]any{}, nil
	case "tools/list":
		return map[string]any{"tools": s.Tools()}, nil
	case "tools/call":
		return s.callTool(req.Params)
	default:
		if len(req.Method) > 14 && req.Method[:14] == "notifications/" {
			return nil, nil
		}
		return nil, &rpcError{Code: MethodNotFound, Message: "unknown method " + req.Method}
	}
}

func (s *Server) initialize(raw json.RawMessage) map[string]any {
	var params struct {
		ProtocolVersion string `json:"protocolVersion"`
	}
	_ = json.Unmarshal(raw, &params)

	s.mu.Lock()
	s.version = NegotiateProtocolVersion(params.ProtocolVersion)
	negotiated := s.version
	s.mu.Unlock()

	result := map[string]any{
		"protocolVersion": negotiated,
		"capabilities":    map[string]any{"tools": map[string]any{"listChanged": false}},
		"serverInfo":      map[string]any{"name": s.Name, "version": s.Version},
	}
	if s.Instructions != "" {
		result["instructions"] = s.Instructions
	}
	return result
}

func (s *Server) callTool(raw json.RawMessage) (any, *rpcError) {
	var params struct {
		Name      string         `json:"name"`
		Arguments map[string]any `json:"arguments"`
	}
	if err := json.Unmarshal(raw, &params); err != nil {
		return nil, &rpcError{Code: InvalidParams, Message: "params must be an object"}
	}
	if params.Name == "" {
		return nil, &rpcError{Code: InvalidParams, Message: "'name' is required"}
	}
	if params.Arguments == nil {
		params.Arguments = map[string]any{}
	}

	s.mu.RLock()
	tool, ok := s.tools[params.Name]
	s.mu.RUnlock()
	if !ok {
		return nil, &rpcError{Code: MethodNotFound, Message: "unknown tool " + params.Name}
	}

	if problems := ValidateArguments(tool.InputSchema, params.Arguments); len(problems) > 0 {
		sort.Strings(problems)
		return nil, &rpcError{Code: InvalidParams, Message: joinProblems(problems)}
	}

	value, err := tool.handler(params.Arguments)
	if err != nil {
		return map[string]any{
			"content": []map[string]any{{"type": "text", "text": err.Error()}},
			"isError": true,
		}, nil
	}
	return map[string]any{"content": ToContent(value), "isError": false}, nil
}

// ToContent normalises a tool return value into MCP content blocks.
func ToContent(value any) []map[string]any {
	switch typed := value.(type) {
	case nil:
		return []map[string]any{{"type": "text", "text": ""}}
	case string:
		return []map[string]any{{"type": "text", "text": typed}}
	case []map[string]any:
		if len(typed) > 0 {
			if _, ok := typed[0]["type"]; ok {
				return typed
			}
		}
	}
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return []map[string]any{{"type": "text", "text": fmt.Sprintf("%v", value)}}
	}
	return []map[string]any{{"type": "text", "text": string(encoded)}}
}

// NegotiateProtocolVersion echoes a revision we support, else our newest.
func NegotiateProtocolVersion(requested string) string {
	for _, supported := range SupportedProtocolVersions {
		if requested == supported {
			return requested
		}
	}
	return DefaultProtocolVersion
}

func encode(r response) []byte {
	out, err := json.Marshal(r)
	if err != nil {
		return []byte(`{"jsonrpc":"2.0","id":null,"error":{"code":-32603,"message":"encoding failed"}}`)
	}
	return out
}

func joinProblems(problems []string) string {
	out := problems[0]
	for _, p := range problems[1:] {
		out += "; " + p
	}
	return out
}
