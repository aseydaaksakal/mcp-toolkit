// Command example-server is a runnable MCP server over stdio.
//
//	go run ./cmd/example-server
//
// Then send it a line of JSON-RPC on stdin, for example:
//
//	{"jsonrpc":"2.0","id":1,"method":"tools/list"}
package main

import (
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/aseydaaksakal/mcp-toolkit/go/mcp"
)

func main() {
	server := mcp.NewServer("example-server", "0.1.0")
	server.Instructions = "Demo server. Every tool is in-memory and side-effect free."

	orders := map[string]string{"ORD-1": "shipped", "ORD-2": "processing"}

	server.Tool("lookup_order", "Look up the status of an order by its identifier.",
		mcp.ObjectSchema(map[string]mcp.Property{
			"order_id": {Type: "string", Description: `Internal order identifier, e.g. "ORD-1".`},
		}, []string{"order_id"}),
		func(args map[string]any) (any, error) {
			id, _ := args["order_id"].(string)
			status, ok := orders[id]
			if !ok {
				return nil, fmt.Errorf("no order named %q", id)
			}
			return map[string]any{"id": id, "status": status}, nil
		})

	server.Tool("shout", "Uppercase a string. Useful for checking the wiring.",
		mcp.ObjectSchema(map[string]mcp.Property{
			"text": {Type: "string", Description: "The text to uppercase."},
		}, []string{"text"}),
		func(args map[string]any) (any, error) {
			text, _ := args["text"].(string)
			return strings.ToUpper(text), nil
		})

	if err := server.Serve(os.Stdin, os.Stdout); err != nil {
		log.Fatalf("example-server: %v", err)
	}
}
