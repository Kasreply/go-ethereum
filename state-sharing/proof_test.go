package statesharing

import (
	"bytes"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/rawdb"
	"github.com/ethereum/go-ethereum/trie"
	"github.com/ethereum/go-ethereum/triedb"
)

func TestStateProof(t *testing.T) {
	db := rawdb.NewMemoryDatabase()
	trieDB := triedb.NewDatabase(db, nil)

	tr, err := trie.New(trie.TrieID(common.Hash{}), trieDB)
	if err != nil {
		t.Fatalf("failed to create trie: %v", err)
	}

	key := []byte("account-key")
	value := []byte("account-state")

	if err := tr.Update(key, value); err != nil {
		t.Fatalf("failed to update trie: %v", err)
	}

	root := tr.Hash()

	proof := rawdb.NewMemoryDatabase()

	if err := tr.Prove(key, proof); err != nil {
		t.Fatalf("failed to create proof: %v", err)
	}

	got, err := VerifyStateProof(root, key, proof)
	if err != nil {
		t.Fatalf("failed to verify proof: %v", err)
	}

	if !bytes.Equal(got, value) {
		t.Fatalf(
			"verified value mismatch: got %q, want %q",
			got,
			value,
		)
	}

	t.Logf(
		"successfully verified state proof: key=%q value=%q root=%s",
		key,
		got,
		root.Hex(),
	)
}
