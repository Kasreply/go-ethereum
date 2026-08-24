package statesharing

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestLocalStateAccess(t *testing.T) {
	node := NewStateStore(NewOwnership(4, 6))

	address := common.HexToAddress("0x1234567890123456789012345678901234567890")
	value := []byte("hello state")

	if err := node.Put(address, value); err != nil {
		t.Fatalf("failed to store state: %v", err)
	}

	got, err := node.GetState(address, common.Hash{})
	if err != nil {
		t.Fatalf("failed to retrieve state: %v", err)
	}

	if string(got) != string(value) {
		t.Fatalf("expected %q, got %q", value, got)
	}

	t.Logf("successfully retrieved local state: %q", got)
}

func TestRemoteStateAccess(t *testing.T) {
	node6 := NewStateStore(NewOwnership(4, 6))
	node2 := NewStateStore(NewOwnership(4, 2))

	node6.SetPeer(node2)

	var address common.Address
	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})

		if node2.ownership.BelongsToAddress(candidate) {
			address = candidate
			break
		}
	}

	if !node2.ownership.BelongsToAddress(address) {
		t.Fatal("could not find an address belonging to node 2")
	}

	if node6.ownership.BelongsToAddress(address) {
		t.Fatal("test address unexpectedly belongs to node 6")
	}

	value := []byte("remote state")

	if err := node2.Put(address, value); err != nil {
		t.Fatalf("failed to store state on node 2: %v", err)
	}

	// Learn the trusted root the way an honest caller would: from a
	// source independent of the peer being queried (here, simulated
	// by asking node2 directly ahead of time).
	honestProof, err := node2.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to learn trusted root: %v", err)
	}

	got, err := node6.GetState(address, honestProof.Root)
	if err != nil {
		t.Fatalf("failed to retrieve remote state: %v", err)
	}

	if string(got) != string(value) {
		t.Fatalf("expected %q, got %q", value, got)
	}

	t.Logf(
		"successfully retrieved remote state %q from node 2 for address %s",
		got,
		address.Hex(),
	)
}

// maliciousPeer simulates a peer that returns a valid proof
// structure but an invalid Merkle root.
type maliciousPeer struct {
	state *RemoteState
}

func (p *maliciousPeer) GetStateProof(key common.Address) (*RemoteState, error) {
	return p.state, nil
}

func TestRemoteStateRejectsTamperedProof(t *testing.T) {
	node2 := NewStateStore(NewOwnership(4, 2))

	var address common.Address
	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})

		if node2.ownership.BelongsToAddress(candidate) {
			address = candidate
			break
		}
	}

	value := []byte("remote state")

	if err := node2.Put(address, value); err != nil {
		t.Fatalf("failed to store state on node 2: %v", err)
	}

	remote, err := node2.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to generate remote proof: %v", err)
	}

	// The trusted root a caller would have learned independently
	// (e.g. from a synced block header) before this proof was
	// tampered with.
	trustedRoot := remote.Root

	// Tamper with the root. The proof nodes still belong to the
	// original root, so verification must fail.
	remote.Root[0] ^= 0xff

	node6 := NewStateStore(NewOwnership(4, 6))
	node6.SetPeer(&maliciousPeer{state: remote})

	_, err = node6.GetState(address, trustedRoot)
	if err == nil {
		t.Fatal("expected tampered proof to be rejected, but it was accepted")
	}

	t.Logf("successfully rejected tampered remote proof: %v", err)
}

// TestRemoteStateRejectsForgedConsistentRoot demonstrates the gap
// that a bare Proof.Root == Root check does NOT cover: a dishonest
// peer that returns a completely different but internally-consistent
// (root, proof) pair. VerifyStateProof alone would happily accept
// this, since the proof genuinely matches the (fake) root it claims.
// Only comparing against an independently-sourced trustedRoot catches it.
func TestRemoteStateRejectsForgedConsistentRoot(t *testing.T) {
	node2 := NewStateStore(NewOwnership(4, 2))

	var address common.Address
	for i := byte(0); i < 255; i++ {
		candidate := common.BytesToAddress([]byte{
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, 0, 0, 0, 0, 0,
			0, 0, 0, i,
		})

		if node2.ownership.BelongsToAddress(candidate) {
			address = candidate
			break
		}
	}

	if err := node2.Put(address, []byte("real state")); err != nil {
		t.Fatalf("failed to store real state: %v", err)
	}

	// Learn the trusted root independently of the (later, malicious) peer.
	honestRemote, err := node2.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to generate honest proof: %v", err)
	}
	trustedRoot := honestRemote.Root

	// A forger with the same ownership range but different data. Its
	// (root, proof) pair is entirely self-consistent -- it would pass
	// a Proof.Root == Root check on its own.
	forger := NewStateStore(NewOwnership(4, 2))
	if err := forger.Put(address, []byte("forged state")); err != nil {
		t.Fatalf("failed to store forged state: %v", err)
	}

	forgedRemote, err := forger.GetStateProof(address)
	if err != nil {
		t.Fatalf("failed to generate forged proof: %v", err)
	}

	if forgedRemote.Root == trustedRoot {
		t.Fatal("test setup invalid: forged root accidentally matches trusted root")
	}

	node6 := NewStateStore(NewOwnership(4, 6))
	node6.SetPeer(&maliciousPeer{state: forgedRemote})

	_, err = node6.GetState(address, trustedRoot)
	if err == nil {
		t.Fatal("expected forged but internally-consistent root/proof pair to be rejected, but it was accepted")
	}

	t.Logf("successfully rejected forged consistent root: %v", err)
}
