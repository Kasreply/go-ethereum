package statesharing

import (
	"math/big"
	"sort"
	"sync"

	"github.com/ethereum/go-ethereum/common"
)

// NodeInfo represents a descriptor for a network node.
type NodeInfo struct {
	ID        common.Address
	PrefixLen uint
	Store     *StateStore
	RT        *RoutingTable
}

// XORMetric calculates the distance metric between two 160-bit identifiers.
func XORMetric(a, b common.Address) *big.Int {
	var res [20]byte
	for i := 0; i < 20; i++ {
		res[i] = a[i] ^ b[i]
	}
	return new(big.Int).SetBytes(res[:])
}

// RoutingTable manages Kademlia-style k-buckets based on prefix length.
type RoutingTable struct {
	self    NodeInfo
	k       int
	buckets [160][]NodeInfo
	mu      sync.RWMutex
}

func NewRoutingTable(self NodeInfo, k int) *RoutingTable {
	if k <= 0 {
		k = 20
	}
	return &RoutingTable{
		self: self,
		k:    k,
	}
}

func logDistance(a, b common.Address) int {
	for byteIdx := 0; byteIdx < 20; byteIdx++ {
		xor := a[byteIdx] ^ b[byteIdx]
		if xor != 0 {
			for bitIdx := 7; bitIdx >= 0; bitIdx-- {
				if (xor & (1 << bitIdx)) != 0 {
					return byteIdx*8 + (7 - bitIdx)
				}
			}
		}
	}
	return 159
}

// AddNode adds or updates a node in the routing table.
func (rt *RoutingTable) AddNode(node NodeInfo) {
	if node.ID == rt.self.ID {
		return
	}
	rt.mu.Lock()
	defer rt.mu.Unlock()

	bucketIdx := logDistance(rt.self.ID, node.ID)
	bucket := rt.buckets[bucketIdx]

	for i, existing := range bucket {
		if existing.ID == node.ID {
			rt.buckets[bucketIdx] = append(append(bucket[:i], bucket[i+1:]...), node)
			return
		}
	}

	if len(bucket) < rt.k {
		rt.buckets[bucketIdx] = append(bucket, node)
	}
}

// ClosestNodes returns up to N nodes closest to target according to XOR distance.
func (rt *RoutingTable) ClosestNodes(target common.Address, n int) []NodeInfo {
	rt.mu.RLock()
	defer rt.mu.RUnlock()

	var allNodes []NodeInfo
	for _, b := range rt.buckets {
		allNodes = append(allNodes, b...)
	}

	sort.Slice(allNodes, func(i, j int) bool {
		distI := XORMetric(allNodes[i].ID, target)
		distJ := XORMetric(allNodes[j].ID, target)
		return distI.Cmp(distJ) < 0
	})

	if len(allNodes) > n {
		return allNodes[:n]
	}
	return allNodes
}

// SearchNetwork implements iterative search (Algorithm 1) from the paper.
func (rt *RoutingTable) SearchNetwork(target common.Address) (*RemoteState, error) {
	visited := make(map[common.Address]bool)
	queue := rt.ClosestNodes(target, rt.k)

	for len(queue) > 0 {
		curr := queue[0]
		queue = queue[1:]

		if visited[curr.ID] {
			continue
		}
		visited[curr.ID] = true

		// 1. Try to get value from current peer
		if curr.Store != nil {
			remote, err := curr.Store.GetStateProof(target)
			if err == nil && remote != nil {
				return remote, nil
			}
		}

		// 2. Iterative discovery: query peer's routing table for closer neighbors
		if curr.RT != nil {
			closer := curr.RT.ClosestNodes(target, rt.k)
			for _, neighbor := range closer {
				if !visited[neighbor.ID] {
					queue = append(queue, neighbor)
				}
			}
			// Keep queue sorted by proximity to target
			sort.Slice(queue, func(i, j int) bool {
				distI := XORMetric(queue[i].ID, target)
				distJ := XORMetric(queue[j].ID, target)
				return distI.Cmp(distJ) < 0
			})
		}
	}
	return nil, ErrNotFound
}
