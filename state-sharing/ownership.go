package statesharing

import (
	"crypto/sha256"

	"github.com/ethereum/go-ethereum/common"
)

type Ownership struct {
	PrefixLength uint
	NodePrefix   uint64
}

func NewOwnership(prefixLength uint, nodePrefix uint64) Ownership {
	return Ownership{
		PrefixLength: prefixLength,
		NodePrefix:   nodePrefix,
	}
}

// PrefixOf computes the top prefixLength bits of key's SHA-256 hash, as a
// uint64. This is the same computation BelongsToNode uses internally to
// compare against NodePrefix, exposed so callers can determine which
// node-prefix a key belongs to without needing to hold that node's own
// Ownership value (e.g. a router dispatching to peers by prefix, or a
// demo deriving the same address independently on two different nodes).
//
// Returns 0 if prefixLength is 0 or greater than 64.
func PrefixOf(key []byte, prefixLength uint) uint64 {
	if prefixLength == 0 || prefixLength > 64 {
		return 0
	}

	hash := sha256.Sum256(key)

	var prefix uint64
	for i := uint(0); i < prefixLength; i++ {
		bit := (hash[i/8] >> (7 - (i % 8))) & 1
		prefix = (prefix << 1) | uint64(bit)
	}

	return prefix
}

func (o Ownership) BelongsToNode(key []byte) bool {
	if o.PrefixLength == 0 {
		return true
	}

	if o.PrefixLength > 64 {
		return false
	}

	return PrefixOf(key, o.PrefixLength) == o.NodePrefix
}

func (o Ownership) BelongsToAddress(addr common.Address) bool {
	return o.BelongsToNode(addr.Bytes())
}

// FindAddressWithPrefix deterministically searches for an address whose
// PrefixOf value matches nodePrefix under the given prefixLength. Because
// the search order and hash function are fixed, any two callers asking
// for the same (prefixLength, nodePrefix) pair independently arrive at
// the same address -- used by the demo so a requesting node can name the
// address it wants without first asking the owning node what it has.
func FindAddressWithPrefix(prefixLength uint, nodePrefix uint64) (common.Address, bool) {
	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})
		if PrefixOf(candidate.Bytes(), prefixLength) == nodePrefix {
			return candidate, true
		}
	}
	return common.Address{}, false
}
