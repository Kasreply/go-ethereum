package statesharing

import (
	"errors"
	"sync"

	"github.com/ethereum/go-ethereum/common"
)

// CacheEntry represents an item stored in the LFU cache.
type CacheEntry struct {
	Value     []byte
	Frequency int
}

// LFUCache provides frequency-based caching for state values (§3.6).
type LFUCache struct {
	capacity int
	items    map[common.Address]*CacheEntry
	mu       sync.RWMutex
}

func NewLFUCache(capacity int) *LFUCache {
	return &LFUCache{
		capacity: capacity,
		items:    make(map[common.Address]*CacheEntry),
	}
}

func (c *LFUCache) Get(key common.Address) ([]byte, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()

	entry, ok := c.items[key]
	if !ok {
		return nil, false
	}
	entry.Frequency++
	return append([]byte(nil), entry.Value...), true
}

func (c *LFUCache) Put(key common.Address, value []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if entry, ok := c.items[key]; ok {
		entry.Value = append([]byte(nil), value...)
		entry.Frequency++
		return
	}

	if len(c.items) >= c.capacity && c.capacity > 0 {
		var minKey common.Address
		minFreq := int(^uint(0) >> 1)
		for k, v := range c.items {
			if v.Frequency < minFreq {
				minFreq = v.Frequency
				minKey = k
			}
		}
		delete(c.items, minKey)
	}

	c.items[key] = &CacheEntry{
		Value:     append([]byte(nil), value...),
		Frequency: 1,
	}
}

// Transaction represents an abstracted transaction with accessed state keys.
type Transaction struct {
	Hash          common.Hash
	AccessedState []common.Address
	Updates       map[common.Address][]byte
}

// Block represents a proposed or received block.
type Block struct {
	Number       uint64
	ParentRoot   common.Hash
	Transactions []Transaction
	StateRoot    common.Hash
}

// ValidatorNode implements the full state sharing node lifecycle (§3.4, App. B).
type ValidatorNode struct {
	ID        common.Address
	Store     *StateStore
	Cache     *LFUCache
	RT        *RoutingTable
	Peers     map[common.Address]*ValidatorNode
	mu        sync.RWMutex
}

func NewValidatorNode(id common.Address, ownership Ownership, prefixLen uint, cacheCap int) *ValidatorNode {
	store := NewStateStore(ownership)
	node := &ValidatorNode{
		ID:    id,
		Store: store,
		Cache: NewLFUCache(cacheCap),
		Peers: make(map[common.Address]*ValidatorNode),
	}
	info := NodeInfo{ID: id, PrefixLen: prefixLen, Store: store}
	node.RT = NewRoutingTable(info, 20)
	info.RT = node.RT
	return node
}

// ProposeBlock implements Algorithm 2: Block Production.
func (v *ValidatorNode) ProposeBlock(txs []Transaction, parentRoot common.Hash) (*Block, error) {
	executionState := make(map[common.Address][]byte)

	// 1. Gather all required state
	for _, tx := range txs {
		for _, addr := range tx.AccessedState {
			if _, exists := executionState[addr]; exists {
				continue
			}

			// Local store check
			if v.Store.ownership.BelongsToAddress(addr) {
				val, err := v.Store.GetState(addr, parentRoot)
				if err == nil {
					executionState[addr] = val
					continue
				}
			}

			// Cache check
			if val, ok := v.Cache.Get(addr); ok {
				executionState[addr] = val
				continue
			}

			// DHT Search
			remote, err := v.RT.SearchNetwork(addr)
			if err != nil {
				return nil, err
			}
			val, err := VerifyRemoteState(remote, addr.Bytes(), parentRoot)
			if err != nil {
				return nil, err
			}
			executionState[addr] = val
			v.Cache.Put(addr, val)
		}
	}

	// 2. Execute block and build updates
	for _, tx := range txs {
		for k, val := range tx.Updates {
			executionState[k] = val
		}
	}

	block := &Block{
		ParentRoot:   parentRoot,
		Transactions: txs,
	}

	// 3. Process and commit local changes
	v.ProcessBlock(block, executionState)

	return block, nil
}

// ProcessBlock implements Algorithm 3: Block Execution.
func (v *ValidatorNode) ProcessBlock(block *Block, executionState map[common.Address][]byte) {
	for addr, val := range executionState {
		if v.Store.ownership.BelongsToAddress(addr) {
			v.Store.Put(addr, val)
		} else {
			v.Cache.Put(addr, val)
		}
	}
}

// ReceiveBlock implements Algorithm 4: Block Gossiping and direct sender fallback.
func (v *ValidatorNode) ReceiveBlock(block *Block, sender *ValidatorNode) error {
	executionState := make(map[common.Address][]byte)

	for _, tx := range block.Transactions {
		for _, addr := range tx.AccessedState {
			if _, ok := executionState[addr]; ok {
				continue
			}

			if v.Store.ownership.BelongsToAddress(addr) {
				val, err := v.Store.GetState(addr, block.ParentRoot)
				if err == nil {
					executionState[addr] = val
					continue
				}
			}

			if val, ok := v.Cache.Get(addr); ok {
				executionState[addr] = val
				continue
			}

			// Direct fetch from block sender
			var remote *RemoteState
			var err error
			if sender != nil && sender.Store != nil {
				remote, err = sender.Store.GetStateProof(addr)
			}
			// Fallback to Kademlia lookup
			if err != nil || remote == nil {
				remote, err = v.RT.SearchNetwork(addr)
				if err != nil {
					return errors.New("failed to retrieve missing block state")
				}
			}

			val, err := VerifyRemoteState(remote, addr.Bytes(), block.ParentRoot)
			if err != nil {
				return err
			}
			executionState[addr] = val
			v.Cache.Put(addr, val)
		}
	}

	// Apply updates
	for _, tx := range block.Transactions {
		for k, val := range tx.Updates {
			executionState[k] = val
		}
	}

	v.ProcessBlock(block, executionState)
	return nil
}
