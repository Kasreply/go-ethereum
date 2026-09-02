package statesharing

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestHTTPServerClientRoundTrip(t *testing.T) {
	node := NewStateStore(NewOwnership(4, 2))
	address := findOwnedAddress(t, node)
	value := []byte("http round trip value")

	if err := node.Put(address, value); err != nil {
		t.Fatalf("failed to store value: %v", err)
	}

	server := httptest.NewServer(NewServer(node).Handler())
	defer server.Close()

	peer := NewHTTPPeer(server.URL)

	remote, err := peer.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to fetch state proof over HTTP: %v", err)
	}

	if !bytes.Equal(remote.Value, value) {
		t.Fatalf("value mismatch: expected %q, got %q", value, remote.Value)
	}

	// Full end-to-end check: verify exactly the way GetState does,
	// using the root learned independently (here, directly from the
	// same store ahead of time, standing in for a locally-synced
	// block header in a real deployment).
	honest, err := node.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to learn trusted root: %v", err)
	}

	if _, err := VerifyRemoteState(remote, address.Bytes(), honest.Root); err != nil {
		t.Fatalf("HTTP-transported remote state failed verification: %v", err)
	}
}

func TestHTTPServerClientViaStateStorePeer(t *testing.T) {
	// Same scenario as TestRemoteStateAccess in store_test.go, but with
	// an HTTPPeer standing in for the in-process peer, exercising the
	// full StateStore.GetState -> Peer -> HTTP path.
	owner := NewStateStore(NewOwnership(4, 2))
	address := findOwnedAddress(t, owner)
	value := []byte("remote state via http")

	if err := owner.Put(address, value); err != nil {
		t.Fatalf("failed to store value on owning node: %v", err)
	}

	server := httptest.NewServer(NewServer(owner).Handler())
	defer server.Close()

	requester := NewStateStore(NewOwnership(4, 6))
	requester.SetPeer(NewHTTPPeer(server.URL))

	honest, err := owner.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to learn trusted root: %v", err)
	}

	got, err := requester.GetState(address, honest.Root)
	if err != nil {
		t.Fatalf("failed to retrieve remote state over HTTP: %v", err)
	}
	if !bytes.Equal(got, value) {
		t.Fatalf("value mismatch: expected %q, got %q", value, got)
	}
}

func TestHTTPServerInvalidAddress(t *testing.T) {
	node := NewStateStore(NewOwnership(4, 2))
	server := httptest.NewServer(NewServer(node).Handler())
	defer server.Close()

	resp, err := http.Get(server.URL + "/state/not-a-valid-address")
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, resp.StatusCode)
	}
}

func TestHTTPServerUnownedAddress(t *testing.T) {
	node := NewStateStore(NewOwnership(4, 2))
	server := httptest.NewServer(NewServer(node).Handler())
	defer server.Close()

	// Find an address that does NOT belong to this node.
	var foreign common.Address
	found := false
	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})
		if !node.ownership.BelongsToAddress(candidate) {
			foreign = candidate
			found = true
			break
		}
	}
	if !found {
		t.Fatal("could not find an address not belonging to node")
	}

	peer := NewHTTPPeer(server.URL)
	_, err := peer.GetStateProof(foreign)
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected ErrNotFound for unowned address, got: %v", err)
	}
}

func TestHTTPServerUnknownOwnedAddress(t *testing.T) {
	// An address that belongs to the node's partition but was never
	// Put, so GetStateProof hits ErrNotFound rather than an ownership
	// error. The server must still respond 404 either way.
	node := NewStateStore(NewOwnership(4, 2))
	address := findOwnedAddress(t, node)

	server := httptest.NewServer(NewServer(node).Handler())
	defer server.Close()

	peer := NewHTTPPeer(server.URL)
	_, err := peer.GetStateProof(address)
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected ErrNotFound for unknown address, got: %v", err)
	}
}

func TestHTTPClientMalformedResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("this is not valid JSON"))
	}))
	defer server.Close()

	peer := NewHTTPPeer(server.URL)
	_, err := peer.GetStateProof(common.HexToAddress("0x1234567890123456789012345678901234567890"))
	if err == nil {
		t.Fatal("expected error decoding malformed response, got nil")
	}
}

func TestHTTPClientUnexpectedStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer server.Close()

	peer := NewHTTPPeer(server.URL)
	_, err := peer.GetStateProof(common.HexToAddress("0x1234567890123456789012345678901234567890"))
	if err == nil {
		t.Fatal("expected error for unexpected status code, got nil")
	}
}
