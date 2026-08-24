package statesharing

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestLocalStateAccess(t *testing.T) {
	// Node 6 owns prefix 0110.
	node := NewStateStore(NewOwnership(4, 6))

	address := common.HexToAddress("0x1234567890123456789012345678901234567890")

	value := []byte("hello state")

	if err := node.Put(address, value); err != nil {
		t.Fatalf("failed to store state: %v", err)
	}

	got, err := node.GetState(address)
	if err != nil {
		t.Fatalf("failed to retrieve state: %v", err)
	}

	if string(got) != string(value) {
		t.Fatalf("expected %q, got %q", value, got)
	}

	t.Logf("successfully retrieved local state: %q", got)
}

func TestRemoteStateAccess(t *testing.T) {
	// Node 6 owns prefix 0110.
	node6 := NewStateStore(NewOwnership(4, 6))

	// Node 2 owns prefix 0010.
	node2 := NewStateStore(NewOwnership(4, 2))

	// Connect node 6 to node 2.
	node6.SetPeer(node2)

	// Find an address that belongs to node 2.
	var address common.Address

	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})

		if node2.ownership.BelongsToAddress(candidate) {
			address = candidate
			break
		}
	}

	if !node2.ownership.BelongsToAddress(address) {
		t.Fatal("could not find an address belonging to node 2")
	}

	if node6.ownership.BelongsToAddress(address) {
		t.Fatal("test address unexpectedly belongs to node 6")
	}

	// Store the state on node 2.
	value := []byte("remote state")

	if err := node2.Put(address, value); err != nil {
		t.Fatalf("failed to store state on node 2: %v", err)
	}

	// Ask node 6 for the state.
	// Node 6 should realize it does not own the address
	// and forward the request to node 2.
	got, err := node6.GetState(address)

	if err != nil {
		t.Fatalf("failed to retrieve remote state: %v", err)
	}

	if string(got) != string(value) {
		t.Fatalf("expected %q, got %q", value, got)
	}

	t.Logf("successfully retrieved remote state %q from node 2 for address %s", got, address.Hex())
}
