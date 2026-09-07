package mcp

import "fmt"

// Property describes one tool argument.
type Property struct {
	Type        string
	Description string
	Enum        []any
	Items       map[string]any
}

func (p Property) toMap() map[string]any {
	out := map[string]any{}
	if p.Type != "" {
		out["type"] = p.Type
	}
	if p.Description != "" {
		out["description"] = p.Description
	}
	if p.Enum != nil {
		out["enum"] = p.Enum
	}
	if p.Items != nil {
		out["items"] = p.Items
	}
	return out
}

// ObjectSchema builds the inputSchema object MCP expects.
//
//	mcp.ObjectSchema(map[string]mcp.Property{
//	    "order_id": {Type: "string", Description: "Internal order identifier."},
//	}, []string{"order_id"})
func ObjectSchema(properties map[string]Property, required []string) map[string]any {
	props := map[string]any{}
	for name, property := range properties {
		props[name] = property.toMap()
	}
	schema := map[string]any{
		"type":                 "object",
		"properties":           props,
		"additionalProperties": false,
	}
	if len(required) > 0 {
		schema["required"] = required
	}
	return schema
}

// ValidateArguments checks presence, primitive type, enum membership and
// unknown keys. Same deliberately small surface as the Python version: it
// catches what models get wrong without pulling in a schema library.
func ValidateArguments(schema map[string]any, arguments map[string]any) []string {
	var problems []string

	properties, _ := schema["properties"].(map[string]any)

	if required, ok := schema["required"].([]string); ok {
		for _, name := range required {
			if _, present := arguments[name]; !present {
				problems = append(problems, fmt.Sprintf("missing required argument %q", name))
			}
		}
	}

	if additional, ok := schema["additionalProperties"].(bool); ok && !additional {
		for name := range arguments {
			if _, declared := properties[name]; !declared {
				problems = append(problems, fmt.Sprintf("unexpected argument %q", name))
			}
		}
	}

	for name, value := range arguments {
		property, ok := properties[name].(map[string]any)
		if !ok {
			continue
		}
		problems = append(problems, checkValue(name, value, property)...)
	}
	return problems
}

func checkValue(name string, value any, property map[string]any) []string {
	var problems []string

	if expected, ok := property["type"].(string); ok && !matchesJSONType(value, expected) {
		problems = append(problems,
			fmt.Sprintf("%q must be %s, got %T", name, expected, value))
	}

	if allowed, ok := property["enum"].([]any); ok {
		found := false
		for _, candidate := range allowed {
			if candidate == value {
				found = true
				break
			}
		}
		if !found {
			problems = append(problems, fmt.Sprintf("%q is not one of the allowed values", name))
		}
	}

	if items, ok := property["items"].(map[string]any); ok {
		if list, isList := value.([]any); isList {
			for index, item := range list {
				problems = append(problems,
					checkValue(fmt.Sprintf("%s[%d]", name, index), item, items)...)
			}
		}
	}
	return problems
}

// matchesJSONType compares against encoding/json's decoded shapes: every
// number arrives as float64, so "integer" means a float64 with no fraction.
func matchesJSONType(value any, expected string) bool {
	switch expected {
	case "string":
		_, ok := value.(string)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "number":
		_, ok := value.(float64)
		return ok
	case "integer":
		number, ok := value.(float64)
		return ok && number == float64(int64(number))
	case "array":
		_, ok := value.([]any)
		return ok
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "null":
		return value == nil
	default:
		return true
	}
}
