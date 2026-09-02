package statesharing

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/rawdb"
	"github.com/ethereum/go-ethereum/ethdb"
)

// ProofEntry is a single key/value pair from a proof database, in a form
// that can be JSON encoded. Key and Value are marshaled as base64 by the
// standard encoding/json []byte handling; this trades some payload size
// for a simple, dependency-free wire format.
type ProofEntry struct {
	Key   []byte `json:"key"`
	Value []byte `json:"value"`
}

// RemoteStateWire is the over-the-wire representation of a RemoteState.
// Proof.Nodes (an ethdb.KeyValueReader) cannot be JSON encoded directly,
// so its contents are flattened into Proof here and reconstructed into a
// fresh in-memory database on the receiving end.
type RemoteStateWire struct {
	Value     []byte       `json:"value"`
	Root      common.Hash  `json:"root"`
	ProofRoot common.Hash  `json:"proofRoot"`
	Proof     []ProofEntry `json:"proof"`
}

// encodeProofNodes flattens every key/value pair held in an
// ethdb.KeyValueReader into a slice of ProofEntry values suitable for JSON
// encoding. The reader must also implement ethdb.Iteratee (true for the
// rawdb/memorydb-backed stores this package uses) since KeyValueReader
// alone offers no way to enumerate its contents.
func encodeProofNodes(nodes ethdb.KeyValueReader) ([]ProofEntry, error) {
	if nodes == nil {
		return nil, errors.New("proof nodes reader is nil")
	}

	it, ok := nodes.(ethdb.Iteratee)
	if !ok {
		return nil, errors.New("proof store does not support iteration")
	}

	iter := it.NewIterator(nil, nil)
	defer iter.Release()

	var entries []ProofEntry
	for iter.Next() {
		entries = append(entries, ProofEntry{
			Key:   common.CopyBytes(iter.Key()),
			Value: common.CopyBytes(iter.Value()),
		})
	}
	if err := iter.Error(); err != nil {
		return nil, err
	}

	return entries, nil
}

// decodeProofNodes rebuilds a fresh in-memory ethdb.KeyValueReader from a
// slice of ProofEntry values previously produced by encodeProofNodes.
func decodeProofNodes(entries []ProofEntry) (ethdb.KeyValueReader, error) {
	db := rawdb.NewMemoryDatabase()
	for _, e := range entries {
		if err := db.Put(e.Key, e.Value); err != nil {
			return nil, err
		}
	}
	return db, nil
}

// toWire converts a RemoteState into its JSON-safe wire representation.
func toWire(remote *RemoteState) (*RemoteStateWire, error) {
	if remote == nil {
		return nil, errors.New("remote state is nil")
	}

	entries, err := encodeProofNodes(remote.Proof.Nodes)
	if err != nil {
		return nil, err
	}

	return &RemoteStateWire{
		Value:     append([]byte(nil), remote.Value...),
		Root:      remote.Root,
		ProofRoot: remote.Proof.Root,
		Proof:     entries,
	}, nil
}

// fromWire reconstructs a RemoteState from its wire representation,
// rebuilding Proof.Nodes as a fresh in-memory database.
func fromWire(wire *RemoteStateWire) (*RemoteState, error) {
	if wire == nil {
		return nil, errors.New("wire state is nil")
	}

	nodes, err := decodeProofNodes(wire.Proof)
	if err != nil {
		return nil, err
	}

	return &RemoteState{
		Value: append([]byte(nil), wire.Value...),
		Root:  wire.Root,
		Proof: Proof{
			Root:  wire.ProofRoot,
			Nodes: nodes,
		},
	}, nil
}
