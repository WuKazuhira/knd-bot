package onebot

import (
	"encoding/json"
	"testing"
)

func TestForwardActionShape(t *testing.T) {
	nodes := []ForwardNode{
		NewForwardNode("tester", 123, Message{ImageBytes("abc")}),
	}
	action := SendGroupForwardAction(456, nodes)
	data, err := json.Marshal(action)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	if got["action"] != "send_group_forward_msg" {
		t.Fatalf("action=%v", got["action"])
	}
	params := got["params"].(map[string]any)
	if params["group_id"] != float64(456) {
		t.Fatalf("params=%v", params)
	}
	messages := params["messages"].([]any)
	node := messages[0].(map[string]any)
	if node["type"] != "node" {
		t.Fatalf("node=%v", node)
	}
	meta := node["data"].(map[string]any)
	if meta["uin"] != "123" || meta["name"] != "tester" {
		t.Fatalf("node data=%v", meta)
	}
}

func TestWithForwardUINDoesNotMutateInput(t *testing.T) {
	nodes := []ForwardNode{NewForwardNode("tester", 0, Message{Text("x")})}
	got := WithForwardUIN(nodes, 999)
	if got[0].Data.UIN != "999" || nodes[0].Data.UIN != "" {
		t.Fatalf("got=%#v input=%#v", got, nodes)
	}
}
