package onebot

import (
	"errors"
	"testing"
)

func TestDecodeMessageEventStringMessage(t *testing.T) {
	raw := []byte(`{"time":1,"self_id":2,"post_type":"message","message_type":"group","message_id":3,"user_id":4,"group_id":5,"message":"hello"}`)

	event, err := DecodeMessageEvent(raw)
	if err != nil {
		t.Fatalf("DecodeMessageEvent() error = %v", err)
	}
	if got := event.Message.PlainText(); got != "hello" {
		t.Fatalf("PlainText() = %q, want hello", got)
	}
}

func TestDecodeMessageEventSegmentArray(t *testing.T) {
	raw := []byte(`{"time":1,"self_id":2,"post_type":"message","message_type":"group","message_id":3,"user_id":4,"group_id":5,"message":[{"type":"at","data":{"qq":123456789}},{"type":"text","data":{"text":"hi"}}]}`)

	event, err := DecodeMessageEvent(raw)
	if err != nil {
		t.Fatalf("DecodeMessageEvent() error = %v", err)
	}
	if got, want := event.Message.PlainText(), "hi"; got != want {
		t.Fatalf("PlainText() = %q, want %q", got, want)
	}
	if got, want := event.Message.AtTargets(), []int64{123456789}; len(got) != len(want) || got[0] != want[0] {
		t.Fatalf("AtTargets() = %v, want %v", got, want)
	}
}

func TestDecodeMessageEventRejectsUnsupportedAndMalformed(t *testing.T) {
	if _, err := DecodeMessageEvent([]byte(`{"post_type":"notice"}`)); !errors.Is(err, ErrUnsupportedEvent) {
		t.Fatalf("unsupported event error = %v, want ErrUnsupportedEvent", err)
	}
	if _, err := DecodeMessageEvent([]byte(`{"post_type":"message"`)); err == nil {
		t.Fatal("malformed JSON unexpectedly decoded")
	}
	if _, err := DecodeMessageEvent([]byte(`{"post_type":"notice"}{}`)); err == nil {
		t.Fatal("multiple JSON values unexpectedly decoded")
	}
}

func TestDecodeMessageEventValidatesRequiredFields(t *testing.T) {
	raw := []byte(`{"post_type":"message","message_type":"group","message_id":3,"user_id":4,"group_id":5,"message":"hello"}`)
	if _, err := DecodeMessageEvent(raw); err == nil {
		t.Fatal("event without self_id unexpectedly decoded")
	}
}

var benchmarkMessageEvent MessageEvent

func BenchmarkDecodeMessageEvent(b *testing.B) {
	cases := map[string][]byte{
		"string":   []byte(`{"time":1,"self_id":2,"post_type":"message","message_type":"group","message_id":3,"user_id":4,"group_id":5,"message":"pjsk info 123"}`),
		"segments": []byte(`{"time":1,"self_id":2,"post_type":"message","message_type":"group","message_id":3,"user_id":4,"group_id":5,"message":[{"type":"at","data":{"qq":123456789}},{"type":"text","data":{"text":" pjsk info 123"}}]}`),
	}
	for name, raw := range cases {
		b.Run(name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				event, err := DecodeMessageEvent(raw)
				if err != nil {
					b.Fatal(err)
				}
				benchmarkMessageEvent = event
			}
		})
	}
}
