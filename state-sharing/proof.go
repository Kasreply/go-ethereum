package statesharing

import (
	"errors"

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

// VerifyRemoteState verifies a RemoteState response against a
// trustedRoot that the caller obtained independently of the peer
// being queried (e.g. the state root recorded in a locally-synced
// block header).
//
// This is the security anchor: remote.Root and remote.Proof.Root
// both come from the peer itself, so a dishonest peer can make them
// agree with each other on a completely fabricated root. Checking
// them against trustedRoot, which the peer does not control, is
// what actually prevents that.
func VerifyRemoteState(
	remote *RemoteState,
	key []byte,
	trustedRoot common.Hash,
) ([]byte, error) {
	if remote.Root != trustedRoot {
		return nil, errors.New("remote state root does not match trusted root")
	}

	if remote.Proof.Root != remote.Root {
		return nil, errors.New("remote proof root does not match remote state root")
	}

	return VerifyStateProof(remote.Root, key, remote.Proof.Nodes)
}
