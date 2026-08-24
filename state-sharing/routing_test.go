package statesharing

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestIterativeKademliaSearch(t *testing.T) {
	id1 := common.HexToAddress("0x0000000000000000000000000000000000000001")
	id2 := common.HexToAddress("0x4000000000000000000000000000000000000002")
	id3 := common.HexToAddress("0x8000000000000000000000000000000000000003")

	s1 := NewStateStore(NewOwnership(1, 0))
	s2 := NewStateStore(NewOwnership(1, 0))
	s3 := NewStateStore(NewOwnership(1, 1))

	// Find a target address owned by node 3 (prefix bit 1)
	var targetAddr common.Address
	for i := 0; i < 256; i++ {
		candidate := common.BytesToAddress([]byte{
			byte(0x80 | i), 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 1,
		})
		if s3.ownership.BelongsToAddress(candidate) {
			targetAddr = candidate
			break
		}
	}

	val := []byte("multi-hop-state")
	if err := s3.Put(targetAddr, val); err != nil {
		t.Fatalf("failed to put state on node 3: %v", err)
	}

	n1 := NodeInfo{ID: id1, PrefixLen: 1, Store: s1}
	n2 := NodeInfo{ID: id2, PrefixLen: 1, Store: s2}
	n3 := NodeInfo{ID: id3, PrefixLen: 1, Store: s3}

	rt1 := NewRoutingTable(n1, 5)
	rt2 := NewRoutingTable(n2, 5)
	rt3 := NewRoutingTable(n3, 5)

	n1.RT = rt1
	n2.RT = rt2
	n3.RT = rt3

	// Wire topology: Node 1 knows Node 2; Node 2 knows Node 3
	rt1.AddNode(n2)
	rt2.AddNode(n3)

	remote, err := rt1.SearchNetwork(targetAddr)
	if err != nil {
		t.Fatalf("iterative SearchNetwork failed: %v", err)
	}

	value, err := VerifyRemoteState(remote, targetAddr.Bytes(), remote.Root)
	if err != nil {
		t.Fatalf("proof verification failed: %v", err)
	}

	if string(value) != string(val) {
		t.Fatalf("expected %q, got %q", val, value)
	}
	t.Logf("successfully discovered and retrieved state via 2-hop routing: %q", string(value))
}
