package statesharing

import (
	"fmt"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func BenchmarkTraceEvaluation(b *testing.B) {
	prefixLengths := []uint{0, 1, 2, 3, 4, 5}

	for _, pl := range prefixLengths {
		b.Run(fmt.Sprintf("PL_%d", pl), func(b *testing.B) {
			nodeAddr := common.HexToAddress("0x0000000000000000000000000000000000000001")
			node := NewValidatorNode(nodeAddr, NewOwnership(pl, 0), pl, 512)

			peerAddr := common.HexToAddress("0x8000000000000000000000000000000000000002")
			peer := NewValidatorNode(peerAddr, NewOwnership(pl, 1), pl, 512)

			node.RT.AddNode(NodeInfo{ID: peerAddr, PrefixLen: pl, Store: peer.Store, RT: peer.RT})

			// Deterministically generate 10 addresses that belong to peer
			var addresses []common.Address
			for i := 0; i < 256 && len(addresses) < 10; i++ {
				candidate := common.BytesToAddress([]byte{
					byte(0x80 | i), 0, 0, 0, 0, 0, 0, 0,
					0, 0, 0, 0, 0, 0, 0, 0,
					0, 0, 0, byte(i),
				})
				if peer.Store.ownership.BelongsToAddress(candidate) {
					peer.Store.Put(candidate, []byte(fmt.Sprintf("state-%d", i)))
					addresses = append(addresses, candidate)
				}
			}

			if len(addresses) == 0 {
				b.Skip("no matching addresses for prefix")
			}

			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				target := addresses[i%len(addresses)]
				if remote, err := node.RT.SearchNetwork(target); err == nil && remote != nil {
					node.Cache.Put(target, remote.Value)
				}
			}
		})
	}
}
