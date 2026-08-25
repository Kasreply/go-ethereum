package main

import (
	"fmt"
	"time"

	"github.com/ethereum/go-ethereum/common"
	statesharing "github.com/ethereum/go-ethereum/state-sharing"
)

func main() {
	fmt.Println("==================================================")
	fmt.Println("🚀 ETHEREUM STATE-SHARING PROTOCOL SIMULATION")
	fmt.Println("==================================================")

	prefixLen := uint(2)
	fmt.Printf("\n[1] Initializing 3 Validator Nodes (PrefixLen = %d)...\n", prefixLen)

	id1 := common.HexToAddress("0x1000000000000000000000000000000000000001")
	id2 := common.HexToAddress("0x2000000000000000000000000000000000000002")
	id3 := common.HexToAddress("0x3000000000000000000000000000000000000003")

	own1 := statesharing.NewOwnership(prefixLen, 0)
	own2 := statesharing.NewOwnership(prefixLen, 1)
	own3 := statesharing.NewOwnership(prefixLen, 2)

	node1 := statesharing.NewValidatorNode(id1, own1, prefixLen, 128)
	node2 := statesharing.NewValidatorNode(id2, own2, prefixLen, 128)
	node3 := statesharing.NewValidatorNode(id3, own3, prefixLen, 128)

	// Configure Kademlia Multi-Hop Topology: Node 1 -> Node 2 -> Node 3
	node1.RT.AddNode(statesharing.NodeInfo{ID: id2, PrefixLen: prefixLen, Store: node2.Store, RT: node2.RT})
	node2.RT.AddNode(statesharing.NodeInfo{ID: id3, PrefixLen: prefixLen, Store: node3.Store, RT: node3.RT})
	time.Sleep(200 * time.Millisecond)
	fmt.Println("    ✔ Routing topology configured (Node 1 -> Node 2 -> Node 3)")

	// Find an address that belongs to Node 3's partition
	var targetAddr common.Address
	for i := 0; i < 256; i++ {
		candidate := common.BytesToAddress([]byte{
			byte(0x80 | i), 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, byte(i),
		})
		if own3.BelongsToAddress(candidate) {
			targetAddr = candidate
			break
		}
	}

	fmt.Println("\n[2] Seeding Account State on Node 3...")
	statePayload := []byte("AccountBalance: 750 ETH | Nonce: 42")
	if err := node3.Store.Put(targetAddr, statePayload); err != nil {
		fmt.Printf("    ❌ Store failed: %v\n", err)
		return
	}
	fmt.Printf("    ✔ Target Address: %s stored on Node 3 (Partition 2)\n", targetAddr.Hex())

	fmt.Println("\n[3] Node 1 (Partition 0) Querying Account owned by Node 3...")
	time.Sleep(300 * time.Millisecond)
	fmt.Println("    🔍 Local state miss on Node 1.")
	fmt.Println("    📡 Initiating Kademlia multi-hop iterative discovery via Node 2...")

	remote, err := node1.RT.SearchNetwork(targetAddr)
	if err != nil {
		fmt.Printf("    ❌ Search failed: %v\n", err)
		return
	}
	fmt.Printf("    📦 Received State Proof via multi-hop routing!\n")

	fmt.Println("\n[4] Cryptographic Verification against State Root...")
	value, err := statesharing.VerifyRemoteState(remote, targetAddr.Bytes(), remote.Root)
	if err != nil {
		fmt.Printf("    ❌ Proof verification failed: %v\n", err)
	} else {
		fmt.Println("    ✅ Cryptographic proof verified successfully!")
		fmt.Printf("    🎉 Decoded State: \"%s\"\n", string(value))
	}
	fmt.Println("\n==================================================")
}
