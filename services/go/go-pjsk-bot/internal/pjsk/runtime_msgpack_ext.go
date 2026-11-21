package pjsk

import (
	"fmt"
	"github.com/vmihailenco/msgpack/v5"
	"reflect"
	"sync"
)

const officialBinaryArrayExtensionID int8 = 42
const officialBinaryArrayMaxSize = 16 << 20

// officialBinaryArray is the opaque payload emitted by RuntimeJsonWriter for
// nativeMeshes/motion typed arrays. It is deliberately never converted to a
// Go slice or interpreted as mesh data.
type officialBinaryArray struct{ bytes []byte }

func (v officialBinaryArray) MarshalMsgpack() ([]byte, error) {
	if len(v.bytes) > officialBinaryArrayMaxSize {
		return nil, fmt.Errorf("typed array payload too large")
	}
	return append([]byte(nil), v.bytes...), nil
}
func (v *officialBinaryArray) UnmarshalMsgpack(data []byte) error {
	if len(data) > officialBinaryArrayMaxSize {
		return fmt.Errorf("typed array payload too large: %d", len(data))
	}
	v.bytes = append(v.bytes[:0], data...)
	return nil
}

var registerOfficialExtOnce sync.Once

func registerOfficialBinaryArrayExtension() {
	registerOfficialExtOnce.Do(func() {
		msgpack.RegisterExtDecoder(officialBinaryArrayExtensionID, officialBinaryArray{}, func(d *msgpack.Decoder, v reflect.Value, n int) error {
			if n > officialBinaryArrayMaxSize {
				return fmt.Errorf("typed array payload too large: %d", n)
			}
			b := make([]byte, n)
			if err := d.ReadFull(b); err != nil {
				return err
			}
			v.Set(reflect.ValueOf(officialBinaryArray{bytes: b}))
			return nil
		})
	})
}
