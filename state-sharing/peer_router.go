package statesharing

import (
	"fmt"

	"github.com/ethereum/go-ethereum/common"
)

// PeerRouter is a Peer that dispatches GetStateProof calls to one of
// several other peers, chosen by the requested key's prefix under
// PrefixLength. It implements the same static, single-hop lookup the
// paper's prototype assumes: no DHT, no multi-hop forwarding -- the
// caller is expected to already know, for each prefix, which peer owns
// it.
type PeerRouter struct {
	PrefixLength uint
	Peers        map[uint64]Peer
}

// GetStateProof implements Peer by computing key's prefix and forwarding
// the request to the peer registered for that prefix.
func (r *PeerRouter) GetStateProof(key common.Address) (*RemoteState, error) {
	prefix := PrefixOf(key.Bytes(), r.PrefixLength)

	peer, ok := r.Peers[prefix]
	if !ok {
		return nil, fmt.Errorf("no known peer owns prefix %d", prefix)
	}

	return peer.GetStateProof(key)
}
