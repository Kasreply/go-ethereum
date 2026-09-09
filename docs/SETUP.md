# Installation and Setup

[Repository](../README.md) · [Project design](PROJECT.md) · [Implementation and demo](IMPLEMENTATION_AND_DEMO.md)

## 1. Prerequisites

Use macOS or Linux with the following tools:

| Requirement | Purpose |
| --- | --- |
| Git | Clone and select the `state-sharing` branch |
| Docker Desktop or Docker Engine | Build/run the four Go services |
| Docker Compose v2 | Run the supplied service topology |
| Python 3.10+ with pip and venv | Run the dashboard; exact compatible versions depend on dependency resolution |
| curl | Check HTTP roots and state |
| Go 1.22+ (optional for Docker-only demo) | Native Go tests/builds; `go.mod` declares Go 1.22 and toolchain go1.22.0 |

The Dockerfile supplies Go 1.22; host Go and `make geth` are not required for the Docker demo. Building ordinary geth natively additionally requires a C compiler. No mainnet sync, funded accounts, Prysm or parent testnet launch is needed.

The dashboard's direct imports require Streamlit, Requests, RLP, trie and pandas. `requirements.txt` also retains pycryptodome as the Ethereum hashing backend dependency. Standard-library modules are not listed. Versions are minimum constraints, not a locked environment.

## 2. Clone or select the branch

New checkout:

```bash
git clone --branch state-sharing https://github.com/Kasreply/go-ethereum.git
cd go-ethereum
```

Existing checkout, from its repository root:

```bash
git status
git fetch origin
git switch state-sharing
```

If the branch exists only remotely:

```bash
git switch --track origin/state-sharing
```

Preserve local changes before switching branches; do not reset or discard them. If Git reports conflicts with local work, resolve that situation before continuing.

Verify the required files:

```bash
ls docker-compose.yml Dockerfile.state-sharing-node
ls state-sharing-dashboard/app.py state-sharing-dashboard/requirements.txt
```

**Distribution note:** the documentation reorganization and dashboard files are pending local review and have not been committed or pushed. A remote clone may not contain this layout until the maintainer publishes it. Obtain the reviewed source files if they are missing; dependency installation cannot recreate them.

This checkout has also been developed within:

```text
state-network-project/state-sharing-protocol/dependencies/go-ethereum
```

From `state-network-project`, enter it with:

```bash
cd state-sharing-protocol/dependencies/go-ethereum
```

Standalone cloning works without that parent layout. The parent `.gitmodules` references a different URL/branch from this fork, so a recursive parent clone must not be assumed to retrieve this exact demo.

## 3. Verify the environment

```bash
git branch --show-current
git remote -v
docker --version
docker compose version
python3 --version
python3 -m pip --version
curl --version
```

Expected branch: `state-sharing`. For native checks also run:

```bash
go version
```

## 4. Build and start Docker nodes

From the repository root containing `docker-compose.yml`:

```bash
docker compose config
docker compose up -d --build
```

The first build needs access to image registries and Go dependencies. Compose builds the custom `state-sharing-node` command directly.

## 5. Verify containers

```bash
docker compose ps
docker ps
```

| Service | Shard / prefix | Expected host → container port |
| --- | --- | --- |
| `node0` | A / 00 | 9550 → 8551 |
| `node1` | B / 01 | 9551 → 8551 |
| `node2` | C / 10 | 9552 → 8551 |
| `node3` | D / 11 | 9553 → 8551 |

All four services should be running. Generated container-name prefixes depend on the Compose project name.

## 6. Test node roots

```bash
curl http://localhost:9550/root
curl http://localhost:9551/root
curl http://localhost:9552/root
curl http://localhost:9553/root
```

Or use a loop:

```bash
for port in 9550 9551 9552 9553
do
  echo "Testing $port"
  curl -fsS "http://localhost:$port/root"
  echo
done
```

Successful output is JSON with a `root` field containing `0x` plus 64 hexadecimal characters. For the default Shard C seed:

```json
{"root":"0xbab2632cdb154f8f846b2cb49399b682f80f001915fbef999c11126282773bb1"}
```

Different shards have different local datasets and roots. These are not canonical world roots.

## 7. Test the state endpoint

```bash
curl -fsS http://localhost:9552/state/0x0000000000000000000000000000000000000004
```

Expect `value`, `root`, `proofRoot` and a `proof` array. `value` is base64 and decodes to `hello from node prefix 2`. Proof entries contain base64 `key`/`value` byte strings. See [the full API example](IMPLEMENTATION_AND_DEMO.md#http-api).

The address must contain 40 hexadecimal characters, conventionally prefixed with `0x`. The short suffix `0004` alone is invalid. Only one address is seeded per shard, so arbitrary well-formed addresses can still return 404.

## 8. Create the dashboard environment

In a second terminal, from the same repository root:

```bash
cd state-sharing-dashboard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The canonical requirements include:

```text
streamlit>=1.30
requests>=2.31
rlp>=5.0
trie>=4.0
pycryptodome>=3.20
pandas>=2.0
```

The project-local dark theme is in `.streamlit/config.toml`; launch from this directory so Streamlit loads it. Existing custom dashboard CSS is preserved.

## 9. Start Streamlit

```bash
python -m streamlit run app.py --server.port 8509
```

Open:

```text
http://localhost:8509
```

For subsequent sessions, from the repository root:

```bash
cd state-sharing-dashboard
source .venv/bin/activate
python -m streamlit run app.py --server.port 8509
```

If a versioned dashboard is already running on 8509, stop it with Ctrl-C in its original terminal, then use the canonical command above. The cleanup does not restart existing processes automatically. Older sources in `archive/` are historical references, not required launch steps.

## 10. Recommended terminal layout

```text
Terminal 1 → Docker / Go services and logs
Terminal 2 → Streamlit dashboard
Terminal 3 → optional curl / tests / debugging
```

If Streamlit is already running, use another terminal for Docker commands.

## 11. Verify the full connection

1. Compose shows all four containers running.
2. All four `/root` endpoints respond.
3. The dashboard shows four reachable nodes and their roots.
4. Select requester Shard A and Account 0004; the owner should be Shard C.
5. A remote state request returns a witness that Proof Explorer can inspect.
6. Root consistency and MPT verification pass for the default seed. **Overall dashboard acceptance currently fails because of the base64/text claim mismatch.** This is an existing implementation issue, not a setup failure.
7. Attack Lab rejects a corrupted required proof node.

Canonical world root should say `Not anchored`, and transaction throughput is not exposed. “ONLINE” measures successful HTTP root retrieval, not consensus health.

## 12. Go tests and optional native builds

From the repository root:

```bash
go test ./state-sharing/...
go test ./core/state -run '^TestRemoteStateDBInterception$' -count=1
```

These cover state-sharing ownership, proof/transport/HTTP behavior, routing/lifecycle experiments and the mock-backed StateDB hook. They do not establish full EVM integration. Results from this cleanup are recorded in [Implementation & Demo](IMPLEMENTATION_AND_DEMO.md#testing-and-validation).

Optional custom binary and in-process demonstration:

```bash
go build -o build/bin/state-sharing-node ./cmd/state-sharing-node
go run ./cmd/state-sharing-demo
```

The latter is a separate three-object in-process simulation using its response root as the verification anchor. It is not the four-node HTTP demo.

Optional existing microbenchmark:

```bash
go test ./state-sharing -run '^$' -bench '^BenchmarkTraceEvaluation$' -benchmem
```

It times synthetic in-process search/proof generation/cache insertion, not real blocks, HTTP or EVM execution. The timed loop does not perform remote proof acceptance verification.

Ordinary upstream geth builds, if needed for other development:

```bash
make geth
make all
```

These require the appropriate native build toolchain and do not enable remote state fetching automatically.

## 13. Docker logs

```bash
docker compose logs
docker compose logs -f
docker compose logs node0
docker compose logs -f node2
```

Each node should log its seed and `listening on :8551`. Node0's automatic lookup should log `DEMO PASSED` and `hello from node prefix 2`. Its phrase “independently-fetched root” means a separate request to node2, not independent canonical authentication. Ctrl-C stops following logs while detached containers continue running.

## 14. Stop the project

Stop Streamlit with Ctrl-C in its terminal. From the Compose directory:

```bash
docker compose down
```

The node store is in memory; restarting reseeds it. No persistent volume is configured.

## 15. Restart the project

If the containers still exist:

```bash
docker compose restart
```

If they were removed with `down`:

```bash
docker compose up -d
```

Then reactivate the dashboard environment and run its canonical startup command. Restart does not rebuild images.

## 16. Rebuild after Go changes

```bash
docker compose up -d --build
docker compose ps
docker compose logs node0
```

If stale images remain a concern after a normal rebuild:

```bash
docker compose build --no-cache
docker compose up -d --force-recreate
```

Repeat root/state checks after rebuilding. Dashboard-only Python changes do not require rebuilding Go images.

## 17. Troubleshooting

| Symptom | Check / action |
| --- | --- |
| Docker daemon unavailable | Start Docker Desktop/Engine; run `docker info` |
| Connection refused / nodes offline | Check `docker compose ps`, service logs and host ports; test `/root` directly |
| Host port already in use | Identify the existing listener; stop that known service or deliberately update both Compose ports and dashboard endpoints |
| Streamlit 8509 occupied | Stop the prior dashboard in its terminal, then launch `app.py` |
| Missing Python package | Activate `.venv`, verify the interpreter, reinstall `requirements.txt` |
| Wrong branch / missing files | Check branch and distribution status; do not discard local work to switch |
| `/state/` returns 400 | Supply the full 20-byte hexadecimal address |
| `/state/` returns 404 | Use the owner endpoint and a seeded address; node0 does not proxy Account 0004 |
| Proof Explorer/Attack Lab empty | Complete a remote lookup in the same session; requester C plus Account 0004 is a local display path and loads no proof |
| Valid proof but overall rejection | Inspect claim encoding; the default mismatch is documented below |
| Old Go behavior after edits | Rebuild with `up -d --build`; restart alone uses the existing image |

Useful environment diagnostics, inside `state-sharing-dashboard`:

```bash
source .venv/bin/activate
python -c 'import sys; print(sys.executable)'
python -m pip --version
python -c 'import streamlit, requests, rlp, trie, pandas; print("Dashboard imports OK")'
python -m pip check
```

If `venv` creation is unavailable, install your operating system's Python venv support, then repeat environment creation. Avoid installing packages into an unrelated interpreter.

### Docker networking

For Streamlit on the host, use `http://localhost:9550` through `:9553`. If you later containerize the dashboard, its `localhost` refers to itself. Join it to the same Compose network and update `NODE_ENDPOINTS` in `app.py` to:

```text
http://node0:8551
http://node1:8551
http://node2:8551
http://node3:8551
```

The supplied Compose file does not containerize Streamlit.

### Normal verification mismatch

The Go JSON claim is base64, but the dashboard compares it with decoded text/bytes. Default response behavior is:

```text
root_match = true
proof_ok = true
claim_match = false
accepted = false
```

Do not disable verification to obtain a green result. For a diagnostic using the correct byte comparison, run this in the activated dashboard environment:

```bash
python - <<'PY'
import base64
import requests
import rlp
from trie import HexaryTrie

address = "0x0000000000000000000000000000000000000004"
endpoint = "http://localhost:9552"
response = requests.get(f"{endpoint}/state/{address}", timeout=2)
response.raise_for_status()
wire = response.json()
response = requests.get(f"{endpoint}/root", timeout=2)
response.raise_for_status()
root = response.json()["root"]
nodes = [rlp.decode(base64.b64decode(e["value"])) for e in wire["proof"]]
value = HexaryTrie.get_from_proof(bytes.fromhex(root[2:]), bytes.fromhex(address[2:]), nodes)
print("Root consistency:", root == wire["root"] == wire["proofRoot"])
print("Proof-derived bytes:", repr(value))
print("Decoded claim matches:", base64.b64decode(wire["value"], validate=True) == value)
PY
```

Expected: `True`, `b'hello from node prefix 2'`, `True`. This diagnostic does not change dashboard behavior or independently authenticate the owner root.

### Understanding activity logs

`Ownership derived` is local hashing; `State response received` and `Owner root fetched` describe actual Python HTTP durations. The A → C trace represents the selected requester, while Python issues the request directly. `Modelled block executed` describes frontend simulation. An overall `MPT verification rejected` event can be caused by the claim mismatch despite successful proof verification. Go process messages are in Compose logs, not the dashboard's session trace.
