package statesharing

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestBlockLifecycleAndGossip(t *testing.T) {
	node1Addr := common.HexToAddress("0x0000000000000000000000000000000000000001")
	node2Addr := common.HexToAddress("0x8000000000000000000000000000000000000002")

	proposer := NewValidatorNode(node1Addr, NewOwnership(1, 0), 1, 10)
	validator := NewValidatorNode(node2Addr, NewOwnership(1, 1), 1, 10)

	// Register routing info
	proposer.RT.AddNode(NodeInfo{ID: node2Addr, PrefixLen: 1, Store: validator.Store, RT: validator.RT})
	validator.RT.AddNode(NodeInfo{ID: node1Addr, PrefixLen: 1, Store: proposer.Store, RT: proposer.RT})

	// Find an address owned by validator (prefix 1)
	var accountKey common.Address
	for i := 0; i < 256; i++ {
		candidate := common.BytesToAddress([]byte{
			byte(0x80 | i), 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 1,
		})
		if validator.Store.ownership.BelongsToAddress(candidate) {
			accountKey = candidate
			break
		}
	}

	initialVal := []byte("initial-balance")
	if err := validator.Store.Put(accountKey, initialVal); err != nil {
		t.Fatalf("failed to store initial balance: %v", err)
	}

	proof, err := validator.Store.GetStateProof(accountKey)
	if err != nil {
		t.Fatalf("failed to obtain trusted proof: %v", err)
	}
	parentRoot := proof.Root

	tx := Transaction{
		Hash:          common.HexToHash("0x1"),
		AccessedState: []common.Address{accountKey},
		Updates:       map[common.Address][]byte{accountKey: []byte("updated-balance")},
	}

	// 1. Proposer fetches missing state, executes, and builds block
	block, err := proposer.ProposeBlock([]Transaction{tx}, parentRoot)
	if err != nil {
		t.Fatalf("failed to propose block: %v", err)
	}

	// 2. Validator receives block, retrieves from proposer/sender, and processes
	if err := validator.ReceiveBlock(block, proposer); err != nil {
		t.Fatalf("validator failed to receive and process block: %v", err)
	}

	// Check updated state on validator
	validator.Store.mu.RLock()
	val, ok := validator.Store.data[accountKey]
	validator.Store.mu.RUnlock()

	if !ok || string(val) != "updated-balance" {
		t.Fatalf("expected updated-balance, got %q", string(val))
	}

	t.Logf("Block successfully proposed, gossiped, and executed with partial state")
}
