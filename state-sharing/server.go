package statesharing

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/ethereum/go-ethereum/common"
)

// Server exposes a StateStore's owned state over HTTP so that other
// processes can retrieve it via HTTPPeer.
//
// Endpoints:
//
//	GET /state/{address}  - value + Merkle proof for one address
//	GET /root             - current state root, no proof attached
//
// Responses (for /state/{address}):
//
//	200 - JSON-encoded RemoteStateWire
//	400 - malformed address
//	404 - address not owned by this node, or no value stored for it
//	500 - internal error (e.g. proof generation or serialization failure)
type Server struct {
	Store *StateStore
}

// NewServer creates an HTTP server backed by the given StateStore.
func NewServer(store *StateStore) *Server {
	return &Server{Store: store}
}

// Handler returns an http.Handler serving the state-sharing endpoints.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/state/", s.handleGetState)
	mux.HandleFunc("/root", s.handleGetRoot)
	return mux
}

// rootResponse is the JSON body returned by GET /root.
type rootResponse struct {
	Root common.Hash `json:"root"`
}

func (s *Server) handleGetRoot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	root, err := s.Store.Root()
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(rootResponse{Root: root})
}

func (s *Server) handleGetState(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	addrHex := strings.TrimPrefix(r.URL.Path, "/state/")
	if addrHex == "" || !common.IsHexAddress(addrHex) {
		http.Error(w, "invalid address", http.StatusBadRequest)
		return
	}
	addr := common.HexToAddress(addrHex)

	remote, err := s.Store.GetStateProof(addr)
	if err != nil {
		// Deliberately collapse "not owned" and "not found" into the same
		// 404 response, so a peer cannot use response codes to map out
		// this node's ownership boundaries more precisely than "does not
		// have it".
		if errors.Is(err, ErrNotFound) || isOwnershipError(err) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if remote == nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	wire, err := toWire(remote)
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(wire)
}

// isOwnershipError reports whether err is the "key does not belong to
// this node" error returned by StateStore.GetStateProof. That error is
// constructed with errors.New at the call site rather than as a sentinel,
// so it is matched by message rather than errors.Is.
func isOwnershipError(err error) bool {
	return err != nil && err.Error() == "key does not belong to this node"
}
