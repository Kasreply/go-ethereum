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

func (o Ownership) BelongsToNode(key []byte) bool {
	if o.PrefixLength == 0 {
		return true
	}

	if o.PrefixLength > 64 {
		return false
	}

	hash := sha256.Sum256(key)

	var prefix uint64
	for i := uint(0); i < o.PrefixLength; i++ {
		bit := (hash[i/8] >> (7 - (i % 8))) & 1
		prefix = (prefix << 1) | uint64(bit)
	}

	return prefix == o.NodePrefix
}

func (o Ownership) BelongsToAddress(addr common.Address) bool {
	return o.BelongsToNode(addr.Bytes())
}
