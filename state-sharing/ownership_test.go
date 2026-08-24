package statesharing

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestOwnership(t *testing.T) {
	key := []byte("hello")

	const prefixLength = uint(4)

	owners := make([]Ownership, 16)

	for i := uint64(0); i < 16; i++ {
		owners[i] = NewOwnership(prefixLength, i)
	}

	count := 0
	owner := uint64(0)

	for i, node := range owners {
		if node.BelongsToNode(key) {
			count++
			owner = uint64(i)
		}
	}

	if count != 1 {
		t.Fatalf("expected exactly one owner, got %d", count)
	}

	t.Logf("key %q belongs to prefix %04b (node %d)", key, owner, owner)
}

func TestAddressOwnership(t *testing.T) {
	address := common.HexToAddress("0x1234567890123456789012345678901234567890")

	const prefixLength = uint(4)

	owners := make([]Ownership, 16)

	for i := uint64(0); i < 16; i++ {
		owners[i] = NewOwnership(prefixLength, i)
	}

	count := 0
	owner := uint64(0)

	for i, node := range owners {
		if node.BelongsToAddress(address) {
			count++
			owner = uint64(i)
		}
	}

	if count != 1 {
		t.Fatalf("expected exactly one owner, got %d", count)
	}

	t.Logf("address %s belongs to prefix %04b (node %d)", address.Hex(), owner, owner)
}
