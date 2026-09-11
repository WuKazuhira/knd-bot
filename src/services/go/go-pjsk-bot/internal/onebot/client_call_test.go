package onebot

import (
	"encoding/json"
	"testing"
)

func TestDispatchRoutesAPIEcho(t *testing.T) {
	client := NewClient("", "", nil, nil)
	ch := make(chan apiResponse, 1)
	client.mu.Lock()
	client.pending["echo-1"] = ch
	client.mu.Unlock()
	client.dispatch([]byte(`{"echo":"echo-1","status":"ok","retcode":0,"data":{"group_id":123}}`))
	response := <-ch
	var data map[string]int
	if err := json.Unmarshal(response.Data, &data); err != nil || data["group_id"] != 123 {
		t.Fatalf("response=%s err=%v", response.Data, err)
	}
}
