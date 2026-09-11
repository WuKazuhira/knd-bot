package remotelive

import "testing"

func TestParseLiveResponseNestedAndWorldBloom(t *testing.T) {
	chapter := 2
	fields, err := ParseLiveResponse([]byte(`{
		"success": true,
		"userLiveId": "live-7",
		"endStatus": 200,
		"endResponse": {
			"result": {"afterEventRanking": {"rank": 12}, "afterEventPoint": "3456", "score": 987},
			"world": [
				{"worldBloomChapterNo": 1, "afterWorldBloomChapterRanking": 99, "afterWorldBloomChapterPoint": 100},
				{"worldBloomChapterNo": 2, "afterWorldBloomChapterRanking": "8", "afterWorldBloomChapterPoint": 222}
			]
		}
	}`), &chapter)
	if err != nil {
		t.Fatal(err)
	}
	if fields.EndStatus == nil || *fields.EndStatus != 200 || fields.LiveID == nil || *fields.LiveID != "live-7" {
		t.Fatalf("root fields=%+v", fields)
	}
	if fields.EventRank == nil || *fields.EventRank != 12 || fields.EventPoint == nil || *fields.EventPoint != 3456 || fields.Score == nil || *fields.Score != 987 {
		t.Fatalf("event fields=%+v", fields)
	}
	if fields.WLChapterNo == nil || *fields.WLChapterNo != 2 || fields.WLChapterRank == nil || *fields.WLChapterRank != 8 || fields.WLChapterPoint == nil || *fields.WLChapterPoint != 222 {
		t.Fatalf("WL fields=%+v", fields)
	}
	if !fields.HasEventResult() {
		t.Fatal("event result should be present")
	}
}

func TestParseLiveResponseEndStatusAndMissingResult(t *testing.T) {
	fields, err := ParseLiveResponse([]byte(`{"endStatus":"404","endResponse":{"afterEventPoint":1}}`), nil)
	if err != nil {
		t.Fatal(err)
	}
	if fields.EndStatus == nil || *fields.EndStatus != 404 || !fields.HasEventResult() {
		t.Fatalf("fields=%+v", fields)
	}
	fields, err = ParseLiveResponse([]byte(`{"success":true}`), nil)
	if err != nil {
		t.Fatal(err)
	}
	if fields.HasEventResult() {
		t.Fatal("missing event result should be false")
	}
}

func TestParseLiveResponseRejectsInvalidJSON(t *testing.T) {
	if _, err := ParseLiveResponse([]byte(`{"endStatus":`), nil); err == nil {
		t.Fatal("invalid JSON should fail")
	}
}
