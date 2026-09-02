package statesharing

import (
	"bytes"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

// findOwnedAddress returns an address that belongs to the given store,
// mirroring the search pattern used in store_test.go.
func findOwnedAddress(t *testing.T, store *StateStore) common.Address {
	t.Helper()

	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})
		if store.ownership.BelongsToAddress(candidate) {
			return candidate
		}
	}
	t.Fatal("could not find an address belonging to store")
	return common.Address{}
}

func TestProofNodeRoundTrip(t *testing.T) {
	node := NewStateStore(NewOwnership(4, 2))
	address := findOwnedAddress(t, node)

	if err := node.Put(address, []byte("proof round trip value")); err != nil {
		t.Fatalf("failed to store value: %v", err)
	}

	remote, err := node.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to generate proof: %v", err)
	}

	entries, err := encodeProofNodes(remote.Proof.Nodes)
	if err != nil {
		t.Fatalf("failed to encode proof nodes: %v", err)
	}
	if len(entries) == 0 {
		t.Fatal("expected at least one proof node entry")
	}

	reconstructed, err := decodeProofNodes(entries)
	if err != nil {
		t.Fatalf("failed to decode proof nodes: %v", err)
	}

	// Every original entry must be retrievable from the reconstructed
	// database with an identical value.
	for _, e := range entries {
		got, err := reconstructed.Get(e.Key)
		if err != nil {
			t.Fatalf("failed to read back key %x: %v", e.Key, err)
		}
		if !bytes.Equal(got, e.Value) {
			t.Fatalf("value mismatch for key %x: expected %x, got %x", e.Key, e.Value, got)
		}
	}

	// The reconstructed proof must still verify against the original
	// root -- this is the actual point of the round trip.
	value, err := VerifyStateProof(remote.Proof.Root, address.Bytes(), reconstructed)
	if err != nil {
		t.Fatalf("reconstructed proof failed to verify: %v", err)
	}
	if !bytes.Equal(value, remote.Value) {
		t.Fatalf("verified value mismatch: expected %x, got %x", remote.Value, value)
	}
}

func TestToWireFromWireRoundTrip(t *testing.T) {
	node := NewStateStore(NewOwnership(4, 2))
	address := findOwnedAddress(t, node)

	if err := node.Put(address, []byte("wire round trip value")); err != nil {
		t.Fatalf("failed to store value: %v", err)
	}

	remote, err := node.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to generate proof: %v", err)
	}

	wire, err := toWire(remote)
	if err != nil {
		t.Fatalf("failed to convert to wire format: %v", err)
	}

	reconstructed, err := fromWire(wire)
	if err != nil {
		t.Fatalf("failed to convert from wire format: %v", err)
	}

	if reconstructed.Root != remote.Root {
		t.Fatalf("root mismatch: expected %s, got %s", remote.Root, reconstructed.Root)
	}
	if reconstructed.Proof.Root != remote.Proof.Root {
		t.Fatalf("proof root mismatch: expected %s, got %s", remote.Proof.Root, reconstructed.Proof.Root)
	}
	if !bytes.Equal(reconstructed.Value, remote.Value) {
		t.Fatalf("value mismatch: expected %x, got %x", remote.Value, reconstructed.Value)
	}

	// The full VerifyRemoteState check (the same one GetState uses)
	// must pass against the reconstructed state, using the original
	// root as the independently trusted root.
	if _, err := VerifyRemoteState(reconstructed, address.Bytes(), remote.Root); err != nil {
		t.Fatalf("reconstructed remote state failed verification: %v", err)
	}
}

func TestEncodeProofNodesNilReader(t *testing.T) {
	if _, err := encodeProofNodes(nil); err == nil {
		t.Fatal("expected error for nil proof nodes reader")
	}
}

func TestDecodeProofNodesEmpty(t *testing.T) {
	db, err := decodeProofNodes(nil)
	if err != nil {
		t.Fatalf("unexpected error decoding empty entries: %v", err)
	}
	if _, err := db.Get([]byte("anything")); err == nil {
		t.Fatal("expected error reading from empty reconstructed database")
	}
}
