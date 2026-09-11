package pjsk

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/vmihailenco/msgpack/v5"
)

func TestDecodeSuite(t *testing.T) {
	payload := suiteFixture(t, "123456789")
	data, err := decodeSuite(payload)
	if err != nil {
		t.Fatal(err)
	}
	if id, ok := nestedString(data, "user", "userRegistration", "userId"); !ok || id != "123456789" {
		t.Fatalf("user id=%q/%v", id, ok)
	}
}

func TestUploadModuleSaveData(t *testing.T) {
	root := t.TempDir()
	fixture := suiteFixture(t, "987654321")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(fixture)
	}))
	defer server.Close()

	module := NewUploadModule(root)
	defer module.Close()
	userID, err := module.saveData(t.Context(), server.URL, 2)
	if err != nil {
		t.Fatal(err)
	}
	if userID != "987654321" {
		t.Fatalf("user id=%q", userID)
	}
	path := filepath.Join(root, "ondemand", "cn", "987654321.json")
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var data map[string]any
	if err := json.Unmarshal(content, &data); err != nil {
		t.Fatal(err)
	}
	if id, ok := nestedString(data, "user", "userRegistration", "userId"); !ok || id != "987654321" {
		t.Fatalf("saved user id=%q/%v", id, ok)
	}
}

func suiteFixture(t *testing.T, userID string) []byte {
	t.Helper()
	payload, err := msgpack.Marshal(map[string]any{
		"user": map[string]any{"userRegistration": map[string]any{"userId": userID}},
	})
	if err != nil {
		t.Fatal(err)
	}
	pad := aes.BlockSize - len(payload)%aes.BlockSize
	for i := 0; i < pad; i++ {
		payload = append(payload, byte(pad))
	}
	block, err := aes.NewCipher([]byte("g2fcC0ZczN9MTJ61"))
	if err != nil {
		t.Fatal(err)
	}
	out := make([]byte, len(payload))
	cipher.NewCBCEncrypter(block, []byte("msx3IV0i9XE5uYZ1")).CryptBlocks(out, payload)
	return out
}
