// Command state-sharing-node runs a single state-sharing node as an
// independent OS process, exposing its owned state over HTTP. Run four
// of these (one per node-prefix 0..3, under PrefixLength=2) to form a
// minimal disjoint-partition state-sharing network with no shared
// process memory and no devp2p -- just static peer endpoints and plain
// HTTP.
//
// One node can additionally be configured (via -demo-target-prefix) to,
// after startup, fetch and cryptographically verify a value owned by
// another node in the network. That is the end-to-end demonstration:
// a real cross-process HTTP request, a real Merkle proof, and a trusted
// root obtained via a call structurally independent of the proof
// response it verifies.
package main

import (
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	statesharing "github.com/ethereum/go-ethereum/state-sharing"
)

func main() {
	var (
		nodePrefix       = flag.Uint64("node-prefix", 0, "this node's NodePrefix value")
		prefixLength     = flag.Uint("prefix-len", 2, "shared PrefixLength across the network")
		httpAddr         = flag.String("http-addr", ":8551", "address this node's HTTP server listens on")
		seedValue        = flag.String("seed", "", "value to store at this node's demo address (default: auto-generated)")
		peersFlag        = flag.String("peers", "", "comma-separated prefix=host:port list for other nodes, e.g. 0=node0:8551,1=node1:8551")
		demoTargetPrefix = flag.Int("demo-target-prefix", -1, "if >= 0, fetch and verify a value owned by this prefix after startup")
		demoDelay        = flag.Duration("demo-delay", 3*time.Second, "initial delay before the demo request, to let peers finish starting")
		demoRetries      = flag.Int("demo-retries", 10, "number of retries for the demo request if peers aren't ready yet")
	)
	flag.Parse()

	if *seedValue == "" {
		*seedValue = fmt.Sprintf("hello from node prefix %d", *nodePrefix)
	}

	ownership := statesharing.NewOwnership(*prefixLength, *nodePrefix)
	store := statesharing.NewStateStore(ownership)

	// Seed a demo address deterministically, so any other node can
	// independently compute the same address without asking us for it.
	addr, ok := statesharing.FindAddressWithPrefix(*prefixLength, *nodePrefix)
	if !ok {
		log.Fatalf("node %d: could not find a demo address for this prefix", *nodePrefix)
	}
	if err := store.Put(addr, []byte(*seedValue)); err != nil {
		log.Fatalf("node %d: failed to seed demo value: %v", *nodePrefix, err)
	}
	log.Printf("node %d: owns %s, seeded value %q", *nodePrefix, addr.Hex(), *seedValue)

	peers, err := parsePeers(*peersFlag)
	if err != nil {
		log.Fatalf("node %d: invalid -peers: %v", *nodePrefix, err)
	}
	delete(peers, *nodePrefix) // never route to ourselves

	if len(peers) > 0 {
		router := &statesharing.PeerRouter{
			PrefixLength: *prefixLength,
			Peers:        peers,
		}
		store.SetPeer(router)
		log.Printf("node %d: configured with %d peer(s)", *nodePrefix, len(peers))
	}

	server := statesharing.NewServer(store)
	go func() {
		log.Printf("node %d: listening on %s", *nodePrefix, *httpAddr)
		if err := http.ListenAndServe(*httpAddr, server.Handler()); err != nil {
			log.Fatalf("node %d: HTTP server failed: %v", *nodePrefix, err)
		}
	}()

	if *demoTargetPrefix >= 0 {
		go runDemo(store, peers, *prefixLength, uint64(*demoTargetPrefix), *demoDelay, *demoRetries)
	}

	select {} // keep the process (and HTTP server) alive
}

// parsePeers parses a "prefix=host:port,prefix=host:port,..." string
// into a map from NodePrefix to an HTTPPeer targeting that endpoint.
func parsePeers(spec string) (map[uint64]statesharing.Peer, error) {
	peers := make(map[uint64]statesharing.Peer)
	if spec == "" {
		return peers, nil
	}

	for _, entry := range strings.Split(spec, ",") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}

		parts := strings.SplitN(entry, "=", 2)
		if len(parts) != 2 {
			return nil, fmt.Errorf("malformed peer entry %q, expected prefix=host:port", entry)
		}

		prefix, err := strconv.ParseUint(strings.TrimSpace(parts[0]), 10, 64)
		if err != nil {
			return nil, fmt.Errorf("malformed prefix in peer entry %q: %w", entry, err)
		}

		hostport := strings.TrimSpace(parts[1])
		if hostport == "" {
			return nil, fmt.Errorf("empty endpoint in peer entry %q", entry)
		}

		peers[prefix] = statesharing.NewHTTPPeer("http://" + hostport)
	}

	return peers, nil
}

// runDemo waits for peers to be reachable, then fetches and verifies a
// value owned by targetPrefix, logging the outcome. It retries because
// in a Compose network there's no guarantee peer containers are already
// accepting connections when this one starts.
func runDemo(store *statesharing.StateStore, peers map[uint64]statesharing.Peer, prefixLength uint, targetPrefix uint64, delay time.Duration, retries int) {
	time.Sleep(delay)

	targetPeer, ok := peers[targetPrefix]
	if !ok {
		log.Printf("DEMO: no configured peer for target prefix %d, cannot run demo", targetPrefix)
		return
	}
	httpPeer, ok := targetPeer.(*statesharing.HTTPPeer)
	if !ok {
		log.Printf("DEMO: peer for target prefix %d is not an HTTPPeer, cannot fetch its root", targetPrefix)
		return
	}

	addr, ok := statesharing.FindAddressWithPrefix(prefixLength, targetPrefix)
	if !ok {
		log.Printf("DEMO: could not derive a demo address for target prefix %d", targetPrefix)
		return
	}

	var (
		trustedRoot common.Hash
		value       []byte
		lastErr     error
	)

	for attempt := 1; attempt <= retries; attempt++ {
		// Step 1: learn the trusted root via a call structurally
		// separate from the proof-carrying request below.
		root, err := statesharing.FetchRoot(httpPeer.Endpoint, nil)
		if err != nil {
			lastErr = fmt.Errorf("fetching trusted root: %w", err)
			time.Sleep(time.Second)
			continue
		}
		trustedRoot = root

		// Step 2: request the value + proof over HTTP, and verify it
		// against the independently-obtained root.
		got, err := store.GetState(addr, trustedRoot)
		if err != nil {
			lastErr = fmt.Errorf("fetching and verifying remote state: %w", err)
			if errors.Is(err, statesharing.ErrNotFound) {
				break // not a transient failure, retrying won't help
			}
			time.Sleep(time.Second)
			continue
		}

		value = got
		lastErr = nil
		break
	}

	if lastErr != nil {
		log.Printf("DEMO FAILED: could not retrieve+verify state owned by prefix %d: %v", targetPrefix, lastErr)
		return
	}

	log.Printf(
		"DEMO PASSED: retrieved address %s (owned by prefix %d) via HTTP, verified against independently-fetched root %s, value = %q",
		addr.Hex(), targetPrefix, trustedRoot, string(value),
	)
}
