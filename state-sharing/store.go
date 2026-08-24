package statesharing

import (
	"errors"
	"sync"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/rawdb"
	"github.com/ethereum/go-ethereum/trie"
	"github.com/ethereum/go-ethereum/triedb"
)

var ErrNotFound = errors.New("state value not found")

// RemoteState contains a value together with the information
// needed to verify that value against a state root.
type RemoteState struct {
	Value []byte
	Root  common.Hash
	Proof Proof
}

// Peer represents another state-sharing node.
// For now this is an in-process mock.
// Later this will become an actual network peer.
type Peer interface {
	GetStateProof(key common.Address) (*RemoteState, error)
}

// StateStore represents the state owned by one node.
type StateStore struct {
	ownership Ownership
	data      map[common.Address][]byte
	peer      Peer
	mu        sync.RWMutex
}

// NewStateStore creates a state store for a node.
func NewStateStore(ownership Ownership) *StateStore {
	return &StateStore{
		ownership: ownership,
		data:      make(map[common.Address][]byte),
	}
}

// SetPeer connects this node to another node.
func (s *StateStore) SetPeer(peer Peer) {
	s.peer = peer
}

// Put stores a value locally.
func (s *StateStore) Put(key common.Address, value []byte) error {
	if !s.ownership.BelongsToAddress(key) {
		return errors.New("key does not belong to this node")
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	s.data[key] = append([]byte(nil), value...)
	return nil
}

// GetStateProof returns a locally stored value together with
// its Merkle proof.
func (s *StateStore) GetStateProof(key common.Address) (*RemoteState, error) {
	if !s.ownership.BelongsToAddress(key) {
		return nil, errors.New("key does not belong to this node")
	}

	s.mu.RLock()
	defer s.mu.RUnlock()

	value, ok := s.data[key]
	if !ok {
		return nil, ErrNotFound
	}

	// Create a trie database for the proof.
	diskdb := rawdb.NewMemoryDatabase()
	trieDB := triedb.NewDatabase(diskdb, nil)

	tr, err := trie.New(trie.TrieID(common.Hash{}), trieDB)
	if err != nil {
		return nil, err
	}

	// Add all locally stored state to the trie.
	for address, state := range s.data {
		if err := tr.Update(address.Bytes(), state); err != nil {
			return nil, err
		}
	}

	root := tr.Hash()

	// Generate a Merkle proof for the requested address.
	proof := rawdb.NewMemoryDatabase()

	if err := tr.Prove(key.Bytes(), proof); err != nil {
		return nil, err
	}

	return &RemoteState{
		Value: append([]byte(nil), value...),
		Root:  root,
		Proof: Proof{
			Root:  root,
			Nodes: proof,
		},
	}, nil
}

// GetState retrieves state.
//
// If the key belongs to this node, it is read locally.
// Otherwise, the request is forwarded to the peer and the returned
// state proof is verified before the value is accepted.
//
// trustedRoot must come from a source the caller trusts
// independently of the peer being queried -- e.g. the state root
// recorded in a locally-synced block header. It must NOT be derived
// from any field on the peer's own response.
func (s *StateStore) GetState(key common.Address, trustedRoot common.Hash) ([]byte, error) {
	if s.ownership.BelongsToAddress(key) {
		s.mu.RLock()
		defer s.mu.RUnlock()

		value, ok := s.data[key]
		if !ok {
			return nil, ErrNotFound
		}

		return append([]byte(nil), value...), nil
	}

	if s.peer == nil {
		return nil, errors.New("state belongs to another node, but no peer is configured")
	}

	remote, err := s.peer.GetStateProof(key)
	if err != nil {
		return nil, err
	}

	if remote == nil {
		return nil, errors.New("peer returned empty state response")
	}

	value, err := VerifyRemoteState(remote, key.Bytes(), trustedRoot)
	if err != nil {
		return nil, err
	}

	if value == nil {
		return nil, ErrNotFound
	}

	return append([]byte(nil), value...), nil
}
