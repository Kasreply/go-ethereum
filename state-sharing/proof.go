package statesharing

import (
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/ethdb"
	"github.com/ethereum/go-ethereum/trie"
)

// Proof represents a Merkle proof returned by a remote state node.
type Proof struct {
	Root  common.Hash
	Nodes ethdb.KeyValueReader
}

// VerifyStateProof verifies a Merkle proof against a state root.
func VerifyStateProof(
	root common.Hash,
	key []byte,
	proof ethdb.KeyValueReader,
) ([]byte, error) {
	return trie.VerifyProof(root, key, proof)
}
