package statesharing

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/ethereum/go-ethereum/common"
)

// HTTPPeer is a Peer implementation that retrieves state from another
// node's Server over HTTP. It preserves the existing Peer interface, so
// it can be passed directly to StateStore.SetPeer in place of an
// in-process mock.
type HTTPPeer struct {
	// Endpoint is the base URL of the remote node's state-sharing server,
	// e.g. "http://localhost:8551". No trailing slash.
	Endpoint string

	// Client is used to make the request. If nil, a default client with a
	// short timeout is used.
	Client *http.Client
}

// NewHTTPPeer creates an HTTPPeer targeting the given endpoint.
func NewHTTPPeer(endpoint string) *HTTPPeer {
	return &HTTPPeer{
		Endpoint: endpoint,
		Client:   &http.Client{Timeout: 5 * time.Second},
	}
}

// GetStateProof implements Peer by issuing an HTTP GET against the
// remote node's /state/{address} endpoint and reconstructing a
// RemoteState from the JSON response.
//
// Note: the caller is still responsible for verifying the returned
// RemoteState against an independently obtained trustedRoot (see
// StateStore.GetState / VerifyRemoteState). This method performs no
// verification itself -- it only transports the peer's claims.
func (p *HTTPPeer) GetStateProof(key common.Address) (*RemoteState, error) {
	client := p.Client
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}

	url := fmt.Sprintf("%s/state/%s", p.Endpoint, key.Hex())

	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		// fall through to decode below
	case http.StatusNotFound:
		return nil, ErrNotFound
	default:
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("state-sharing peer returned status %d: %s", resp.StatusCode, string(body))
	}

	var wire RemoteStateWire
	if err := json.NewDecoder(resp.Body).Decode(&wire); err != nil {
		return nil, fmt.Errorf("malformed response from peer: %w", err)
	}

	return fromWire(&wire)
}

// FetchRoot retrieves a node's current state root from its /root
// endpoint. This is a separate call from GetStateProof, so that a
// trusted root can be obtained through a structurally distinct request
// rather than being read off the same response that carries the proof
// being verified against it. See StateStore.Root's doc comment for the
// limits of what that actually buys you.
func FetchRoot(endpoint string, client *http.Client) (common.Hash, error) {
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}

	resp, err := client.Get(endpoint + "/root")
	if err != nil {
		return common.Hash{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return common.Hash{}, fmt.Errorf("state-sharing peer returned status %d: %s", resp.StatusCode, string(body))
	}

	var wire rootResponse
	if err := json.NewDecoder(resp.Body).Decode(&wire); err != nil {
		return common.Hash{}, fmt.Errorf("malformed response from peer: %w", err)
	}

	return wire.Root, nil
}
