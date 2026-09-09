"""
Ethereum Distributed State Lab — Dashboard V6 Command Center

This dashboard is an operator/explorer UI over the existing four-node
state-sharing demo plus a clearly-labelled execution research simulator. It deliberately distinguishes between:

1. LIVE protocol features backed by the existing Go HTTP API:
   - node health/root lookup
   - prefix ownership calculation
   - remote GET /state/{address}
   - independent MPT proof verification
   - proof tampering demonstration
   - request/proof timing and size measurements

2. PLANNED protocol features that require Go-side changes:
   - canonical/global root anchoring
   - structured Ethereum account state
   - state transitions / transaction execution
   - cross-shard batch proof endpoints
   - proof-node deduplication benchmarks

Run the four Go nodes first, then:
    python -m streamlit run app.py --server.port 8509
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import requests
import rlp
import streamlit as st
import pandas as pd
from trie import HexaryTrie


# ---------------------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------------------
NODE_ENDPOINTS = {
    0: "http://localhost:9550",
    1: "http://localhost:9551",
    2: "http://localhost:9552",
    3: "http://localhost:9553",
}
PREFIX_LENGTH = 2
REQUESTER_PREFIX = 0

SHARDS = {
    0: {"name": "Shard A", "label": "Alpha", "prefix": "00"},
    1: {"name": "Shard B", "label": "Beta", "prefix": "01"},
    2: {"name": "Shard C", "label": "Gamma", "prefix": "10"},
    3: {"name": "Shard D", "label": "Delta", "prefix": "11"},
}

DEFAULT_ADDRESS = "0x0000000000000000000000000000000000000004"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def normalize_address(address_hex: str) -> str:
    value = address_hex.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    if len(value) != 40:
        raise ValueError("Account ID must contain exactly 40 hexadecimal characters.")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("Account ID must contain hexadecimal characters only.") from exc
    return "0x" + value


def short_hash(value: str | None, head: int = 8, tail: int = 6) -> str:
    if not value:
        return "Unavailable"
    if len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}…{value[-tail:]}"


def human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def safe_decode_value(value: bytes | None) -> str:
    if value is None:
        return "No value returned"
    if value == b"":
        return "Empty value"
    try:
        text = value.decode("utf-8")
        if text.isprintable():
            return text
    except UnicodeDecodeError:
        pass
    return "0x" + value.hex()


def prefix_details(address_hex: str) -> dict[str, Any]:
    address = normalize_address(address_hex)
    addr_bytes = bytes.fromhex(address[2:])
    digest = hashlib.sha256(addr_bytes).digest()
    digest_hex = "0x" + digest.hex()
    first_byte_binary = f"{digest[0]:08b}"

    prefix = 0
    for i in range(PREFIX_LENGTH):
        byte_index = i // 8
        bit_pos = 7 - (i % 8)
        bit = (digest[byte_index] >> bit_pos) & 1
        prefix = (prefix << 1) | bit

    return {
        "address": address,
        "digest_hex": digest_hex,
        "first_byte_binary": first_byte_binary,
        "prefix_bits": first_byte_binary[:PREFIX_LENGTH],
        "owner_prefix": prefix,
    }


def friendly_account_name(address: str) -> str:
    # Human-facing alias only; never used for ownership or verification.
    return f"Account {address[-4:].upper()}"


def log_event(level: str, message: str) -> None:
    if "event_log" not in st.session_state:
        st.session_state.event_log = []
    st.session_state.event_log.append({"ts": time.strftime("%H:%M:%S"), "level": level, "message": message})
    st.session_state.event_log = st.session_state.event_log[-120:]


def render_event_console(limit: int = 14) -> None:
    events = st.session_state.get("event_log", [])[-limit:]
    if not events:
        st.markdown("<div class='console'><span class='ts'>waiting</span>  No dashboard events yet.</div>", unsafe_allow_html=True)
        return
    lines = []
    for event in events:
        level = event.get("level", "info")
        safe_message = str(event.get("message", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"<div><span class='ts'>{event['ts']}</span> <span class='{level}'>{level.upper():>5}</span>  {safe_message}</div>")
    st.markdown("<div class='console'>" + "".join(lines) + "</div>", unsafe_allow_html=True)


def status_class(online: bool) -> str:
    return "online" if online else "offline"

# ---------------------------------------------------------------------------
# HTTP + proof verification
# ---------------------------------------------------------------------------
@dataclass
class TimedResponse:
    payload: Any
    elapsed_ms: float
    raw_bytes: int


def fetch_root(endpoint: str, timeout: float = 2.0) -> TimedResponse:
    started = time.perf_counter()
    resp = requests.get(f"{endpoint}/root", timeout=timeout)
    elapsed_ms = (time.perf_counter() - started) * 1000
    resp.raise_for_status()
    return TimedResponse(
        payload=resp.json()["root"],
        elapsed_ms=elapsed_ms,
        raw_bytes=len(resp.content),
    )


def fetch_state(endpoint: str, address: str, timeout: float = 2.0) -> TimedResponse:
    started = time.perf_counter()
    resp = requests.get(f"{endpoint}/state/{address}", timeout=timeout)
    elapsed_ms = (time.perf_counter() - started) * 1000
    resp.raise_for_status()
    return TimedResponse(
        payload=resp.json(),
        elapsed_ms=elapsed_ms,
        raw_bytes=len(resp.content),
    )


def check_node_online(endpoint: str):
    try:
        result = fetch_root(endpoint)
        return True, result.payload, result.elapsed_ms
    except Exception:
        return False, None, None


def decode_wire_proof(wire_proof: list) -> list:
    return [rlp.decode(base64.b64decode(entry["value"])) for entry in wire_proof]


def verify_proof(trusted_root_hex: str, address_hex: str, wire_proof: list):
    root_bytes = bytes.fromhex(trusted_root_hex[2:])
    address_bytes = bytes.fromhex(normalize_address(address_hex)[2:])
    started = time.perf_counter()
    try:
        decoded_nodes = decode_wire_proof(wire_proof)
        value = HexaryTrie.get_from_proof(root_bytes, address_bytes, decoded_nodes)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return True, value, None, elapsed_ms, decoded_nodes
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return False, None, f"{type(exc).__name__}: {exc}", elapsed_ms, []


def tamper_proof(wire_proof: list) -> list:
    tampered = copy.deepcopy(wire_proof)
    if not tampered:
        return tampered
    raw = bytearray(base64.b64decode(tampered[0]["value"]))
    if raw:
        raw[-1] ^= 0xFF
    tampered[0]["value"] = base64.b64encode(bytes(raw)).decode()
    return tampered


def proof_payload_size(wire_proof: list) -> int:
    total = 0
    for entry in wire_proof:
        try:
            total += len(base64.b64decode(entry["value"]))
        except Exception:
            total += len(str(entry).encode("utf-8"))
    return total




# ---------------------------------------------------------------------------
# V4 execution-research simulator (frontend model; not a live EVM backend)
# ---------------------------------------------------------------------------
CACHE_TIERS = ("Account Headers", "Storage Slots", "Contract Bytecode")

def stable_int(*parts: Any, modulo: int = 10000) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % modulo

def demo_key(tx_index: int, key_index: int, key_type: str) -> str:
    digest = hashlib.sha256(f"tx:{tx_index}:key:{key_index}:{key_type}".encode()).hexdigest()
    return "0x" + digest[:40]

def key_owner(key_hex: str, prefix_length: int) -> int:
    if prefix_length <= 0:
        return 0
    digest = hashlib.sha256(bytes.fromhex(key_hex[2:])).digest()
    prefix = 0
    for i in range(prefix_length):
        bit = (digest[i // 8] >> (7 - (i % 8))) & 1
        prefix = (prefix << 1) | bit
    # The live prototype only has four shards. For p>2, fold the logical
    # prefix onto the existing four physical nodes for visualization only.
    return prefix % 4

def cache_key_tier(key_type: str) -> str:
    return {"account": "Account Headers", "slot": "Storage Slots", "bytecode": "Contract Bytecode"}[key_type]

def ensure_demo_cache() -> dict[str, dict[str, dict[str, Any]]]:
    if "demo_cache" not in st.session_state:
        st.session_state.demo_cache = {tier: {} for tier in CACHE_TIERS}
    return st.session_state.demo_cache

def build_demo_block(tx_count: int, prefix_length: int) -> list[dict[str, Any]]:
    keys=[]
    key_types=("account","slot","slot","bytecode")
    for tx in range(tx_count):
        touched = 2 + stable_int("touches", tx, tx_count, modulo=3)
        for j in range(touched):
            kt=key_types[stable_int("kind",tx,j,modulo=len(key_types))]
            key=demo_key(tx,j,kt)
            keys.append({
                "tx": tx+1,
                "key": key,
                "key_type": kt,
                "tier": cache_key_tier(kt),
                "owner": key_owner(key,prefix_length),
                "hinted": stable_int("hint",key,modulo=100) < 78,
                "size_bytes": 96 + stable_int("size",key,modulo=640),
            })
    return keys

def synthetic_proof_nodes(key: str, depth: int = 7) -> list[str]:
    # A deterministic model of shared branch-node identifiers. These are NOT
    # real MPT witness nodes and are labelled as modelled wherever surfaced.
    base=key[2:]
    return [hashlib.sha256(f"{base[:i+2]}:{i}".encode()).hexdigest() for i in range(depth)]

def run_demo_block(tx_count: int, prefix_length: int, requester: int, emulated_rtt_ms: int, cache_capacity: int) -> dict[str, Any]:
    cache=ensure_demo_cache()
    keys=build_demo_block(tx_count,prefix_length)
    seen=set()
    local=[]; cache_hits=[]; remote=[]; prefetched=[]; fallback=[]
    tier_hits={t:0 for t in CACHE_TIERS}; tier_misses={t:0 for t in CACHE_TIERS}
    evictions={t:0 for t in CACHE_TIERS}

    for item in keys:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        if item["owner"] == requester:
            local.append(item); continue
        tier=item["tier"]; pool=cache[tier]
        if item["key"] in pool:
            pool[item["key"]]["freq"] += 1
            pool[item["key"]]["last"] = time.time()
            cache_hits.append(item); tier_hits[tier]+=1
            continue
        tier_misses[tier]+=1
        remote.append(item)
        if item["hinted"]: prefetched.append(item)
        else: fallback.append(item)
        pool[item["key"]] = {"freq":1,"last":time.time(),"bytes":item["size_bytes"],"type":item["key_type"]}
        while len(pool) > cache_capacity:
            victim=min(pool.items(), key=lambda kv:(kv[1]["freq"],kv[1]["last"]))[0]
            del pool[victim]; evictions[tier]+=1

    individual_nodes=[]
    for item in remote:
        individual_nodes.extend(synthetic_proof_nodes(item["key"]))
    unique_nodes=set(individual_nodes)
    # Modelled byte sizes. This is deliberately separate from live proof measurements.
    individual_proof_bytes=len(individual_nodes)*155
    dictionary_proof_bytes=len(unique_nodes)*155 + len(remote)*32
    proof_saving=(1-dictionary_proof_bytes/individual_proof_bytes)*100 if individual_proof_bytes else 0

    remote_by_owner={}
    for item in remote:
        remote_by_owner.setdefault(item["owner"],0); remote_by_owner[item["owner"]]+=1
    rpc_batches=len(remote_by_owner)
    measured_cpu_ms=0.22*len(keys)
    fetch_ms=(emulated_rtt_ms * max(1,rpc_batches)) if remote else 0
    parallel_verify_ms=0.08*len(unique_nodes)
    fallback_penalty_ms=emulated_rtt_ms*len(fallback)
    total_ms=measured_cpu_ms + fetch_ms + parallel_verify_ms + fallback_penalty_ms

    return {
        "tx_count":tx_count,"prefix_length":prefix_length,"keys":keys,"unique_keys":len(seen),
        "local":local,"cache_hits":cache_hits,"remote":remote,"prefetched":prefetched,"fallback":fallback,
        "tier_hits":tier_hits,"tier_misses":tier_misses,"evictions":evictions,"cache":cache,
        "individual_nodes":len(individual_nodes),"unique_nodes":len(unique_nodes),
        "individual_proof_bytes":individual_proof_bytes,"dictionary_proof_bytes":dictionary_proof_bytes,
        "proof_saving_pct":proof_saving,"rpc_batches":rpc_batches,"remote_by_owner":remote_by_owner,
        "emulated_rtt_ms":emulated_rtt_ms,"prefetch_coverage":(len(prefetched)/len(remote)*100 if remote else 100.0),
        "fallback_rate":(len(fallback)/len(remote)*100 if remote else 0.0),
        "fetch_ms":fetch_ms,"verify_ms_model":parallel_verify_ms,"evm_cpu_ms_model":measured_cpu_ms,
        "fallback_penalty_ms":fallback_penalty_ms,"total_ms_model":total_ms,
    }

def badge(text: str, kind: str = "modelled") -> str:
    return f"<span class='data-badge {kind}'>{text}</span>"


def run_state_query(address: str, requester_prefix: int) -> dict[str, Any]:
    details = prefix_details(address)
    log_event("info", f"Ownership derived for {short_hash(details['address'], 8, 4)} → {SHARDS[details['owner_prefix']]['name']}")
    owner_prefix = details["owner_prefix"]
    result: dict[str, Any] = {
        **details,
        "requester_prefix": requester_prefix,
        "local": owner_prefix == requester_prefix,
        "started_at": time.time(),
    }

    if result["local"]:
        result["status"] = "local"
        log_event("ok", f"Local ownership confirmed on {SHARDS[requester_prefix]['name']}; no remote RPC required")
        return result

    endpoint = NODE_ENDPOINTS[owner_prefix]
    log_event("info", f"GET /state routed {SHARDS[requester_prefix]['name']} → {SHARDS[owner_prefix]['name']}")
    state_response = fetch_state(endpoint, details["address"])
    log_event("ok", f"State response received in {state_response.elapsed_ms:.2f} ms ({human_bytes(state_response.raw_bytes)})")
    root_response = fetch_root(endpoint)
    log_event("ok", f"Owner root fetched in {root_response.elapsed_ms:.2f} ms")
    wire = state_response.payload
    proof_ok, value, error, verify_ms, decoded_nodes = verify_proof(
        root_response.payload,
        details["address"],
        wire.get("proof", []),
    )

    claimed_value = wire.get("value")
    proof_value_text = safe_decode_value(value)
    if isinstance(claimed_value, str):
        claim_match = claimed_value == proof_value_text or claimed_value.encode("utf-8", errors="ignore") == value
    else:
        claim_match = True if claimed_value is None else claimed_value == value

    root_match = root_response.payload == wire.get("root")

    # V2 deliberately makes every acceptance criterion explicit.
    accepted = bool(root_match and proof_ok and claim_match)

    log_event("ok" if accepted else "err", f"MPT verification {'accepted' if accepted else 'rejected'} in {verify_ms:.3f} ms; proof nodes={len(wire.get('proof', []))}")

    result.update(
        status="remote",
        wire=wire,
        trusted_root=root_response.payload,
        root_match=root_match,
        proof_ok=proof_ok,
        claim_match=claim_match,
        accepted=accepted,
        proof_value=value,
        proof_value_text=proof_value_text,
        error=error,
        decoded_nodes=decoded_nodes,
        state_request_ms=state_response.elapsed_ms,
        root_request_ms=root_response.elapsed_ms,
        verify_ms=verify_ms,
        state_response_bytes=state_response.raw_bytes,
        root_response_bytes=root_response.raw_bytes,
        proof_bytes=proof_payload_size(wire.get("proof", [])),
        proof_nodes=len(wire.get("proof", [])),
    )
    return result


# ---------------------------------------------------------------------------
# Page setup + styling
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Ethereum State Command Center",
    page_icon="◈",
    layout="wide",
)

st.markdown(
    """
<style>
:root {
  --bg: #0d1117;
  --panel: #161b22;
  --panel-2: #0f141b;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #8b949e;
  --green: #3fb950;
  --amber: #d29922;
  --red: #f85149;
  --blue: #58a6ff;
  --purple: #bc8cff;
}
.stApp { background: var(--bg); color: var(--text); }
[data-testid="stHeader"] { background: rgba(13,17,23,.78); backdrop-filter: blur(10px); }
[data-testid="stSidebar"] { background: #010409; border-right: 1px solid var(--border); }
.block-container { max-width: 1500px; padding-top: 1.35rem; padding-bottom: 4.5rem; }
h1,h2,h3 { letter-spacing: -0.025em; }
[data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: .82rem 1rem; }
[data-testid="stMetricLabel"] { color: var(--muted); }
[data-testid="stMetricValue"] { font-size: 1.45rem; font-weight: 680; }
[data-testid="stButton"] button { border-radius: 9px; font-weight: 650; }
[data-testid="stExpander"] { border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
code, pre, .mono, .hash-box { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important; }
.small-muted { color: var(--muted); font-size: .84rem; }
.command-kicker { color: var(--blue); font-size: .76rem; font-weight: 750; letter-spacing: .14em; text-transform: uppercase; margin-bottom: .2rem; }
.telemetry-strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; margin:.8rem 0 1.2rem; }
.telemetry-card { background:linear-gradient(180deg,#161b22,#12171e); border:1px solid var(--border); border-radius:12px; padding:.82rem .95rem; min-width:0; }
.telemetry-label { color:var(--muted); font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; font-weight:700; }
.telemetry-value { margin-top:.25rem; font-size:1.05rem; font-weight:700; overflow-wrap:anywhere; }
.node-card { background:linear-gradient(180deg,#161b22,#11161d); border:1px solid var(--border); border-radius:14px; padding:1rem; min-height:184px; position:relative; overflow:hidden; }
.node-card.online { border-color: rgba(63,185,80,.5); }
.node-card.offline { border-color: rgba(248,81,73,.55); }
.node-card.selected { box-shadow: inset 3px 0 0 var(--blue); }
.node-top { display:flex; align-items:center; justify-content:space-between; gap:.65rem; }
.node-name { font-weight:750; font-size:1.06rem; }
.node-sub { color:var(--muted); font-size:.78rem; margin-top:.12rem; }
.status-pill { display:inline-flex; align-items:center; gap:.42rem; border:1px solid var(--border); border-radius:999px; padding:.22rem .5rem; font-size:.7rem; font-weight:750; white-space:nowrap; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
.dot.ok { background:var(--green); box-shadow:0 0 0 3px rgba(63,185,80,.12),0 0 12px rgba(63,185,80,.55); animation:pulse 1.8s ease-in-out infinite; }
.dot.bad { background:var(--red); box-shadow:0 0 0 3px rgba(248,81,73,.12); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.45} }
.node-stat { display:flex; justify-content:space-between; gap:.75rem; margin-top:.7rem; padding-top:.55rem; border-top:1px solid rgba(48,54,61,.72); font-size:.79rem; }
.node-stat span:first-child { color:var(--muted); }
.hash-box { padding:.55rem .65rem; border-radius:8px; background:#010409; border:1px solid var(--border); overflow-wrap:anywhere; color:#c9d1d9; font-size:.78rem; }
.flow-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:.5rem; align-items:stretch; }
.flow-step { padding:.72rem .75rem; border:1px solid var(--border); border-radius:10px; background:var(--panel); margin:0; }
.flow-step b { font-size:.83rem; }
.flow-step .small-muted { display:block; margin-top:.22rem; }
.pass { color:var(--green); } .fail { color:var(--red); }
.console { background:#010409; border:1px solid var(--border); border-radius:12px; padding:.8rem .9rem; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.76rem; line-height:1.55; max-height:270px; overflow:auto; }
.console .ts { color:#6e7681; } .console .ok { color:var(--green); } .console .info { color:var(--blue); } .console .warn { color:var(--amber); } .console .err { color:var(--red); }
.topology-wrap { background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:.5rem .8rem .25rem; }
.proof-row { display:flex; align-items:center; gap:.7rem; background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:.6rem .75rem; margin:.38rem 0; }
.proof-index { width:28px; height:28px; display:grid; place-items:center; border-radius:8px; background:#21262d; font-weight:750; color:var(--blue); flex:0 0 auto; }
.proof-body { min-width:0; flex:1; }
.proof-title { font-weight:650; font-size:.83rem; }
.proof-hash { color:var(--muted); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.74rem; overflow-wrap:anywhere; }
.section-label { margin-top:.4rem; color:var(--muted); font-size:.74rem; text-transform:uppercase; letter-spacing:.09em; font-weight:700; }

.data-badge{display:inline-block;border-radius:999px;padding:.16rem .46rem;font-size:.66rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;border:1px solid var(--border);margin-left:.35rem;vertical-align:middle}.data-badge.live{color:var(--green);border-color:rgba(63,185,80,.45);background:rgba(63,185,80,.08)}.data-badge.emulated{color:var(--amber);border-color:rgba(210,153,34,.45);background:rgba(210,153,34,.08)}.data-badge.modelled{color:var(--purple);border-color:rgba(188,140,255,.45);background:rgba(188,140,255,.08)}.pipeline{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:.42rem;margin:.8rem 0 1rem}.pipe{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:.7rem;min-height:88px}.pipe .num{color:var(--blue);font-weight:800;font-size:.75rem}.pipe .title{font-weight:700;font-size:.82rem;margin-top:.25rem}.pipe .desc{color:var(--muted);font-size:.72rem;margin-top:.25rem}.cache-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem}.cache-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:.85rem}.cache-bar{height:8px;border-radius:999px;background:#21262d;overflow:hidden;margin:.55rem 0}.cache-fill{height:100%;background:var(--blue)}.timeline{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:.8rem}.tl-row{display:grid;grid-template-columns:150px 1fr 80px;gap:.6rem;align-items:center;margin:.35rem 0;font-size:.78rem}.tl-track{height:12px;border-radius:999px;background:#21262d;overflow:hidden}.tl-fill{height:100%;background:var(--purple)}@media(max-width:1000px){.pipeline{grid-template-columns:1fr 1fr 1fr}.cache-grid{grid-template-columns:1fr}.tl-row{grid-template-columns:110px 1fr 65px}}@media(max-width:620px){.pipeline{grid-template-columns:1fr}.tl-row{grid-template-columns:90px 1fr 55px}}

@media(max-width:900px){ .telemetry-strip,.flow-grid{grid-template-columns:1fr 1fr;} }
@media(max-width:580px){ .telemetry-strip,.flow-grid{grid-template-columns:1fr;} }
@media(prefers-reduced-motion:reduce){ .dot.ok{animation:none;} }

/* ------------------------------------------------------------------
   V4 readability pass — force Streamlit controls/content to respect
   the dark command-centre palette instead of theme-derived low
   contrast colors.
   ------------------------------------------------------------------ */
html, body, [class*="css"], .stApp, .stApp p, .stApp li, .stApp span,
.stApp label, .stApp div {
  color: var(--text);
}

/* General typography */
.stMarkdown, .stMarkdown p, .stMarkdown li, .stCaption,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {
  color: #d8dee9 !important;
}
h1, h2, h3, h4, h5, h6,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {
  color: #f0f6fc !important;
}
small, .small-muted, .node-sub, .proof-hash, .section-label,
.telemetry-label, .pipe .desc {
  color: #aeb8c4 !important;
}

/* Sidebar */
[data-testid="stSidebar"] * {
  color: #c9d1d9;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
  color: #f0f6fc !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
  color: #9da7b3 !important;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] [role="radiogroup"] label {
  color: #d8dee9 !important;
  opacity: 1 !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"] div,
[data-testid="stSidebar"] [data-baseweb="select"] div {
  color: #e6edf3 !important;
}
[data-testid="stSidebar"] hr {
  border-color: #21262d !important;
}

/* Selects / inputs */
[data-baseweb="select"] > div,
[data-baseweb="input"] > div,
[data-baseweb="base-input"] {
  background: #161b22 !important;
  border-color: #3b434d !important;
  color: #f0f6fc !important;
}
[data-baseweb="select"] span,
[data-baseweb="input"] input,
[data-baseweb="base-input"] input {
  color: #f0f6fc !important;
  -webkit-text-fill-color: #f0f6fc !important;
}
[data-baseweb="popover"] * {
  color: #f0f6fc !important;
}

/* Metrics */
[data-testid="stMetric"] {
  background: linear-gradient(180deg,#171d25,#131920) !important;
}
[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] * {
  color: #aeb8c4 !important;
  opacity: 1 !important;
}
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] * {
  color: #f0f6fc !important;
  opacity: 1 !important;
}
[data-testid="stMetricDelta"],
[data-testid="stMetricDelta"] * {
  opacity: 1 !important;
}

/* Native Streamlit alert/info boxes */
[data-testid="stAlert"] {
  border: 1px solid #3b434d !important;
}
[data-testid="stAlert"] p,
[data-testid="stAlert"] span,
[data-testid="stAlert"] div {
  color: #e6edf3 !important;
  opacity: 1 !important;
}
div[data-baseweb="notification"] {
  color: #e6edf3 !important;
}

/* Expanders / tabs / buttons */
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary * {
  color: #e6edf3 !important;
}
button, button p, [role="tab"], [role="tab"] p {
  color: #e6edf3 !important;
  opacity: 1 !important;
}

/* Custom cards */
.telemetry-value, .node-name, .node-stat span:last-child,
.proof-title, .pipe .title, .cache-card, .tl-row {
  color: #f0f6fc !important;
}
.node-stat span:first-child {
  color: #aeb8c4 !important;
}
.telemetry-card, .node-card, .flow-step, .proof-row, .pipe,
.cache-card, .timeline, .topology-wrap {
  box-shadow: 0 1px 0 rgba(255,255,255,.025);
}

/* Make research/modelled information vivid but readable */
.data-badge.live { color:#7ee787 !important; }
.data-badge.emulated { color:#e3b341 !important; }
.data-badge.modelled { color:#d2a8ff !important; }
.pass { color:#7ee787 !important; }
.fail { color:#ff7b72 !important; }
.command-kicker { color:#79c0ff !important; }
.console { color:#e6edf3 !important; }
.console .ts { color:#8b949e !important; }
.console .ok { color:#7ee787 !important; }
.console .info { color:#79c0ff !important; }
.console .warn { color:#e3b341 !important; }
.console .err { color:#ff7b72 !important; }

/* Stronger link contrast */
a, a:visited { color:#79c0ff !important; }
a:hover { color:#a5d6ff !important; }

/* Keep intentionally muted content readable rather than near-black */
[aria-disabled="true"], [disabled] {
  opacity: .72 !important;
}


/* ------------------------------------------------------------------
   V4 presentation polish — stronger hierarchy, neon-accented borders,
   larger small text, and presentation-friendly data visualisations.
   ------------------------------------------------------------------ */
:root {
  --cyan:#33d6ff;
  --lime:#7ee787;
  --violet:#c084fc;
  --hot:#ff5c63;
  --panel-border:#465261;
}
.block-container { max-width: 1540px; }
.stApp { font-size: 17px; }
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
.stCaption, [data-testid="stCaptionContainer"] p {
  font-size: 1rem !important;
  line-height: 1.62 !important;
  font-weight: 500 !important;
}
[data-testid="stCaptionContainer"] p { color:#aeb9c8 !important; }
.small-muted,.node-sub,.proof-hash,.pipe .desc,.telemetry-label,.section-label {
  font-size:.88rem !important;
  line-height:1.5 !important;
  font-weight:560 !important;
  color:#b8c2cf !important;
}
[data-testid="stSidebar"] { border-right:1px solid #36414d !important; }
[data-testid="stSidebar"] * { font-size:1rem; }
[data-testid="stSidebar"] [role="radiogroup"] label { font-weight:650 !important; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { font-size:.92rem !important; color:#9ea9b8 !important; }
[data-testid="stSidebar"] hr { border-color:#36414d !important; }

.telemetry-card,.node-card,.flow-step,.proof-row,.pipe,.cache-card,.timeline,.topology-wrap,[data-testid="stMetric"] {
  border-color: var(--panel-border) !important;
  box-shadow:0 10px 28px rgba(0,0,0,.18), inset 0 1px 0 rgba(255,255,255,.025) !important;
}
.telemetry-card:hover,.node-card:hover,.cache-card:hover,.pipe:hover {
  border-color:#64748b !important;
}
.node-card.online { border-color:rgba(126,231,135,.78) !important; box-shadow:0 0 0 1px rgba(126,231,135,.12),0 0 24px rgba(126,231,135,.08)!important; }
.node-card.offline { border-color:rgba(255,92,99,.72) !important; box-shadow:0 0 0 1px rgba(255,92,99,.10),0 0 22px rgba(255,92,99,.07)!important; }
.node-card.selected { border-left:4px solid var(--cyan) !important; }
.node-name { font-size:1.22rem !important; }
.node-stat { font-size:.92rem !important; border-top-color:#36414d !important; }
.status-pill { font-size:.78rem !important; padding:.3rem .58rem !important; }
.command-kicker { color:var(--cyan)!important; font-size:.83rem!important; }
.telemetry-value { font-size:1.18rem!important; }

/* Slider visibility */
[data-testid="stSlider"] label p { font-size:1rem!important; font-weight:650!important; color:#e6edf3!important; }
[data-testid="stSlider"] [data-baseweb="slider"] > div > div { height:7px!important; }
[data-testid="stSlider"] [role="slider"] { box-shadow:0 0 0 4px rgba(51,214,255,.12),0 0 18px rgba(51,214,255,.22)!important; }

/* Dial readout */
.dial-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1rem;margin:.7rem 0 1.1rem}
.dial-card{background:linear-gradient(180deg,#171d25,#121820);border:1px solid var(--panel-border);border-radius:16px;padding:1rem;display:flex;align-items:center;gap:1rem;min-height:132px}
.dial{--pct:50;--accent:var(--cyan);width:94px;height:94px;border-radius:50%;display:grid;place-items:center;flex:0 0 auto;background:conic-gradient(var(--accent) calc(var(--pct)*1%),#26303b 0);position:relative;box-shadow:0 0 24px color-mix(in srgb,var(--accent) 15%,transparent)}
.dial:after{content:"";position:absolute;inset:9px;border-radius:50%;background:#11171e;border:1px solid #3b4654}
.dial-value{position:relative;z-index:1;font-size:1.16rem;font-weight:800;color:#f5f9ff}
.dial-copy b{display:block;font-size:1rem;color:#f0f6fc}.dial-copy span{display:block;color:#aeb9c8;font-size:.84rem;line-height:1.4;margin-top:.25rem}

/* Visual cards and bars replacing white grids */
.coverage-list,.endpoint-grid,.cap-grid{display:grid;gap:.65rem;margin:.7rem 0 1rem}
.coverage-row{display:grid;grid-template-columns:84px 1fr 110px;gap:.8rem;align-items:center;background:#151b23;border:1px solid var(--panel-border);border-radius:12px;padding:.7rem .85rem}
.coverage-track{height:12px;background:#252e38;border-radius:999px;overflow:hidden}.coverage-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--cyan),var(--violet))}
.coverage-row b{font-size:.9rem}.coverage-row span{color:#bac4d1;font-size:.88rem}
.endpoint-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.endpoint-card{background:#151b23;border:1px solid var(--panel-border);border-radius:14px;padding:.9rem 1rem}.endpoint-card b{font-size:1rem}.endpoint-meta{color:#aeb9c8;font-size:.86rem;line-height:1.55;margin-top:.35rem}.endpoint-status{float:right;font-size:.72rem;font-weight:800;border-radius:999px;padding:.18rem .45rem;border:1px solid currentColor}
.cap-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.cap-card{background:linear-gradient(180deg,#151d29,#111821);border:1px solid #36516e;border-radius:14px;padding:1rem}.cap-card .cap-title{color:var(--cyan);font-weight:800;margin-bottom:.35rem}.cap-card p{margin:0!important;color:#c8d2df!important;font-size:.91rem!important}

/* Guidance / empty states */
.empty-state{border:1.5px solid rgba(24,215,255,.42);border-radius:18px;background:radial-gradient(circle at 10% 0%,rgba(24,215,255,.08),transparent 35%),linear-gradient(180deg,#111b27,#0c131c);padding:1.35rem 1.45rem;margin:.9rem 0;box-shadow:0 0 28px rgba(24,215,255,.06)}.empty-title{font-size:1.18rem;font-weight:850;color:#f5f9ff;letter-spacing:-.01em}.empty-sub{color:#c6d0de;margin-top:.38rem;font-size:.98rem;line-height:1.55}.mini-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.9rem;margin-top:1rem}.mini-step{border:1.5px solid #34485f;border-radius:14px;padding:1rem 1.05rem;background:linear-gradient(180deg,#151f2b,#101923);min-height:128px;box-shadow:inset 0 1px 0 rgba(255,255,255,.025)}.mini-step b{display:block;color:var(--cyan2);font-size:1rem;margin-bottom:.4rem}.mini-step .small-muted{font-size:.93rem;line-height:1.55;color:#c3cedc}

/* Performance comparison cards */
.benchmark-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:.65rem;margin:.9rem 0}.bench-card{background:#151b23;border:1px solid var(--panel-border);border-radius:13px;padding:.8rem}.bench-card strong{display:block;color:#f0f6fc;margin-bottom:.35rem}.bench-card .big{font-size:1.35rem;font-weight:800;color:var(--violet)}.bench-card small{font-size:.78rem;color:#aeb9c8}

/* Code blocks should stay dark */
[data-testid="stCodeBlock"], pre { background:#05080c !important; border-color:#394553 !important; }

@media(max-width:1000px){.dial-grid{grid-template-columns:1fr 1fr}.benchmark-grid{grid-template-columns:1fr 1fr}.endpoint-grid{grid-template-columns:1fr}.cap-grid{grid-template-columns:1fr}}
@media(max-width:650px){.dial-grid,.benchmark-grid{grid-template-columns:1fr}.coverage-row{grid-template-columns:70px 1fr 90px}.mini-flow{grid-template-columns:1fr}}


/* ------------------------------------------------------------------
   V5 presentation polish — progressive disclosure + brighter semantics
   ------------------------------------------------------------------ */
:root{
  --obsidian:#070b12;
  --surface:#101722;
  --surface-2:#151e2b;
  --cyan:#00e7ff;
  --mint:#19f5a6;
  --coral:#ff4f72;
  --violet:#b47cff;
  --amber2:#ffbf5b;
  --text-strong:#f7fbff;
  --text-soft:#bdc8d6;
  --line:#3a4a5f;
}
.stApp{background:radial-gradient(circle at 70% -10%,rgba(0,231,255,.055),transparent 28%),var(--obsidian)!important}
[data-testid="stSidebar"]{background:#03070d!important;border-right:1.5px solid #253247!important}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] span{font-size:.95rem!important;font-weight:600!important;color:#d6deea!important}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:.18rem 0!important}
[data-testid="stSidebar"] hr{border-color:#263446!important}
.small-muted,.node-sub,.endpoint-meta,.dial-copy span,.bench-card small,.proof-hash,.panel-copy{color:var(--text-soft)!important;font-size:.91rem!important;font-weight:540!important}
.node-card,.telemetry-card,.cache-card,.pipe,.cap-card,.endpoint-card,.dial-card,.empty-state,.timeline,.topology-wrap{border-width:1.5px!important;border-color:#33455b!important;box-shadow:0 8px 28px rgba(0,0,0,.18)}
.node-card:hover,.telemetry-card:hover,.cache-card:hover,.cap-card:hover,.endpoint-card:hover{border-color:rgba(0,231,255,.72)!important;box-shadow:0 0 0 1px rgba(0,231,255,.12),0 10px 30px rgba(0,0,0,.25)}
.node-card.online{border-color:rgba(25,245,166,.72)!important}.node-card.offline{border-color:rgba(255,79,114,.74)!important}
.node-card.selected{box-shadow:inset 4px 0 0 var(--cyan),0 0 28px rgba(0,231,255,.10)!important}
.node-name{color:var(--text-strong)!important;font-size:1.28rem!important}
.node-stat{font-size:.94rem!important}.node-stat span:first-child{color:#b7c3d2!important}.node-stat b{color:#f6f9fc!important}
.status-pill{font-size:.8rem!important;background:#0b1119!important}
.telemetry-label{font-size:.78rem!important;color:#aeb9c8!important}.telemetry-value{color:#f8fbff!important;font-size:1.22rem!important}
.command-kicker{font-size:.86rem!important;color:var(--cyan)!important}
.flow-step,.mini-step{border-color:#3a4b61!important;background:#121a25!important}
.flow-step b,.mini-step b{color:#eaf2fb!important}
.cap-card .cap-title{color:var(--cyan)!important}.cap-card:nth-child(2) .cap-title{color:var(--mint)!important}.cap-card:nth-child(3) .cap-title{color:var(--violet)!important}
.cache-fill{background:linear-gradient(90deg,var(--cyan),var(--mint))!important}
.coverage-fill{background:linear-gradient(90deg,var(--mint),var(--cyan))!important}
.tl-fill{background:linear-gradient(90deg,var(--violet),var(--cyan))!important}
[data-testid="stMetric"]{border:1.5px solid #33455b!important;background:linear-gradient(180deg,#131b26,#0e151e)!important}
[data-testid="stMetricLabel"] p{font-size:.9rem!important;font-weight:650!important;color:#b9c5d3!important}
[data-testid="stMetricValue"]{color:#f8fbff!important;font-weight:760!important}
[data-testid="stCaptionContainer"] p{font-size:.92rem!important;font-weight:520!important;color:#aeb8c6!important}
[data-testid="stAlert"] p{font-size:.98rem!important;line-height:1.55!important}
[data-testid="stMarkdownContainer"] p{line-height:1.58}
.stButton button{background:linear-gradient(135deg,#00b7cf,#006f91)!important;border:1px solid #00dff7!important;color:white!important;box-shadow:0 0 20px rgba(0,231,255,.15)!important}
.stButton button:hover{background:linear-gradient(135deg,#00d9f0,#0088ad)!important;box-shadow:0 0 26px rgba(0,231,255,.28)!important}
.nav-guide{border:1px solid #253247;border-radius:12px;padding:.65rem .75rem;margin:.35rem 0 .8rem;background:#09101a;color:#aeb9c8;font-size:.78rem;line-height:1.6}
.nav-guide b{color:#f4f8fc;font-size:.74rem;letter-spacing:.08em}.nav-guide span{color:var(--cyan)}
.story-flow{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:.55rem;margin:.8rem 0 1rem;align-items:stretch}
.story-step{position:relative;background:#111925;border:1.5px solid #33465d;border-radius:13px;padding:.78rem .7rem;text-align:center;min-height:92px}
.story-step:not(:last-child):after{content:'→';position:absolute;right:-.48rem;top:35%;z-index:3;color:var(--cyan);font-weight:900;font-size:1.2rem;text-shadow:0 0 10px rgba(0,231,255,.45)}
.story-step .snum{font-size:.7rem;color:var(--cyan);font-weight:800;letter-spacing:.08em}.story-step .stitle{font-size:.9rem;font-weight:760;color:#f4f8fc;margin-top:.28rem}.story-step .sdesc{font-size:.76rem;color:#aeb9c8;margin-top:.3rem;line-height:1.35}
.legend-row{display:flex;flex-wrap:wrap;gap:.5rem;margin:.45rem 0 1rem}.legend-chip{border:1px solid #33465d;border-radius:999px;padding:.26rem .58rem;font-size:.75rem;font-weight:750;background:#0d141e}.legend-chip.cyan{color:var(--cyan)}.legend-chip.mint{color:var(--mint)}.legend-chip.coral{color:var(--coral)}.legend-chip.violet{color:var(--violet)}.legend-chip.amber{color:var(--amber2)}
.frontier-card{background:#101823;border:1.5px solid #34475f;border-radius:16px;padding:.85rem 1rem 1rem;margin:.65rem 0 1rem}.frontier-head{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;margin-bottom:.45rem}.frontier-title{font-weight:800;color:#f6f9fd}.frontier-sub{font-size:.82rem;color:#aeb9c8}.frontier-legend{font-size:.78rem;white-space:nowrap}.frontier-legend .g{color:var(--mint)}.frontier-legend .c{color:var(--cyan)}
.attack-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;margin:.7rem 0 1rem}.attack-card{background:#121a25;border:1.5px solid #3a4a60;border-radius:14px;padding:.9rem}.attack-card b{color:#f5f9fd}.attack-card .risk{display:block;color:var(--coral);font-size:.72rem;font-weight:800;text-transform:uppercase;letter-spacing:.07em;margin-bottom:.35rem}.attack-card p{color:#b8c3d1!important;font-size:.88rem!important;margin:.3rem 0 0!important}
.cache-card b{font-size:1.03rem;color:#f6f9fd}.cache-explain{color:#aeb9c8;font-size:.83rem;line-height:1.45;margin:.28rem 0 .45rem}
.behind{border-left:3px solid var(--cyan);padding-left:.8rem;color:#b9c5d3;font-size:.91rem;margin:.3rem 0}
@media(max-width:950px){.story-flow{grid-template-columns:1fr 1fr}.story-step:not(:last-child):after{display:none}.attack-grid{grid-template-columns:1fr}}



/* ==================================================================
   V6 command-centre polish
   Fixes overflow, spacing and typography while preserving behaviour.
   ================================================================== */
:root{
  --obsidian:#070b11;
  --surface:#101823;
  --surface2:#121c28;
  --line:#30445d;
  --cyan2:#18d7ff;
  --mint2:#19f5a6;
  --violet2:#b66cff;
  --coral2:#ff4f7d;
  --amber3:#ffb24a;
}
.stApp{background:radial-gradient(circle at 55% -15%,rgba(24,215,255,.045),transparent 28%),var(--obsidian)!important}
.block-container{max-width:1480px!important;padding-left:2.15rem!important;padding-right:2.15rem!important}

/* Type scale: readable but contained */
h1,[data-testid="stMarkdownContainer"] h1{font-size:clamp(2.15rem,4vw,3.45rem)!important;line-height:1.06!important;font-weight:800!important;letter-spacing:-.04em!important}
h2,[data-testid="stMarkdownContainer"] h2{font-size:clamp(1.55rem,2.5vw,2.15rem)!important;line-height:1.15!important;font-weight:800!important}
h3,[data-testid="stMarkdownContainer"] h3{font-size:clamp(1.18rem,1.8vw,1.55rem)!important;line-height:1.2!important;font-weight:760!important}
[data-testid="stMarkdownContainer"] p,[data-testid="stMarkdownContainer"] li{font-size:.96rem!important;line-height:1.55!important}
.stCaption,[data-testid="stCaptionContainer"] p{font-size:.9rem!important;line-height:1.5!important;color:#a9b5c5!important}

/* Sidebar: compact group guide + clearer navigation */
[data-testid="stSidebar"]{background:#050910!important;border-right:1px solid #213249!important}
[data-testid="stSidebar"] .block-container{padding-left:1.05rem!important;padding-right:1.05rem!important}
.nav-guide{display:grid;gap:.42rem;background:linear-gradient(180deg,#0d1622,#09111b);border:1px solid #28405a;border-radius:14px;padding:.75rem .85rem;margin:.8rem 0 .95rem}
.nav-guide>div{display:grid;grid-template-columns:92px 1fr;gap:.45rem;align-items:start}
.nav-guide b{font-size:.68rem!important;letter-spacing:.11em;color:#67cfff!important;white-space:nowrap}
.nav-guide span{font-size:.78rem!important;color:#b9c4d2!important;line-height:1.35}
[data-testid="stSidebar"] [role="radiogroup"]{gap:.08rem!important}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:.24rem 0!important;font-size:.94rem!important;line-height:1.25!important}

/* Responsive shard cards */
.shard-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1rem;margin:.72rem 0 1rem}
.node-card{min-width:0!important;min-height:0!important;height:100%;padding:1rem 1.05rem!important;border-width:1.5px!important;border-radius:18px!important;background:linear-gradient(180deg,#151e29,#0f1721)!important;overflow:hidden!important}
.node-card.shard-0{border-color:rgba(24,215,255,.62)!important;box-shadow:0 0 22px rgba(24,215,255,.06)!important}
.node-card.shard-1{border-color:rgba(182,108,255,.62)!important;box-shadow:0 0 22px rgba(182,108,255,.06)!important}
.node-card.shard-2{border-color:rgba(25,245,166,.58)!important;box-shadow:0 0 22px rgba(25,245,166,.05)!important}
.node-card.shard-3{border-color:rgba(255,79,125,.62)!important;box-shadow:0 0 22px rgba(255,79,125,.06)!important}
.node-card.selected{box-shadow:inset 5px 0 0 var(--cyan2),0 0 28px rgba(24,215,255,.12)!important}
.node-headline{display:flex;justify-content:space-between;align-items:flex-start;gap:.55rem;margin-bottom:.72rem;min-width:0}
.node-name{font-size:1.16rem!important;line-height:1.1!important;white-space:nowrap!important}
.node-sub{font-size:.78rem!important;margin-top:.18rem!important}
.status-pill{font-size:.69rem!important;padding:.28rem .5rem!important;flex:0 0 auto;max-width:48%;overflow:hidden;text-overflow:ellipsis}
.node-kv{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);gap:.55rem;align-items:center;border-top:1px solid #29394b;padding:.52rem 0;font-size:.79rem;min-width:0}
.node-kv span{color:#aeb9c8!important;min-width:0}
.node-kv b{color:#f5f8fc!important;text-align:right;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.79rem}
.prefix-chip{justify-self:end;border:1px solid currentColor;border-radius:7px;padding:.08rem .35rem;color:#dce8f7!important;background:#0b131d}
.endpoint-text{font-size:.72rem!important}

/* Telemetry cards */
.telemetry-strip{gap:1rem!important;margin:1rem 0 1.65rem!important}
.telemetry-card{padding:.95rem 1rem!important;border:1.5px solid #30445d!important;border-radius:15px!important;background:linear-gradient(180deg,#121b26,#0d151f)!important;min-height:92px;display:flex;flex-direction:column;justify-content:center}
.telemetry-label{font-size:.69rem!important;line-height:1.25!important;color:#9fb0c2!important}
.telemetry-value{font-size:1.05rem!important;line-height:1.25!important;overflow-wrap:anywhere!important}

/* Story / pipeline boxes: never exceed bounds */
.story-flow,.pipeline{gap:.78rem!important}
.story-step,.pipe{min-width:0!important;overflow:hidden!important;padding:.9rem .78rem!important}
.stitle,.pipe .title{font-size:.92rem!important;line-height:1.25!important;overflow-wrap:anywhere!important;hyphens:auto}
.sdesc,.pipe .desc{font-size:.79rem!important;line-height:1.45!important;overflow-wrap:anywhere!important}
.story-step:not(:last-child):after{right:-.62rem!important;font-size:1.35rem!important}

/* Dials: vertical composition avoids copy clipping */
.dial-grid{grid-template-columns:repeat(4,minmax(0,1fr))!important;gap:1rem!important;margin:1rem 0 1.4rem!important}
.dial-card{min-width:0!important;min-height:188px!important;display:flex!important;flex-direction:column!important;justify-content:center!important;align-items:center!important;text-align:center!important;gap:.7rem!important;padding:1rem .7rem!important;border:1.5px solid #334961!important;border-radius:18px!important;background:linear-gradient(180deg,#131d29,#0d151f)!important;overflow:hidden!important}
.dial{width:104px!important;height:104px!important}
.dial-value{font-size:1.08rem!important;white-space:nowrap!important}
.dial-copy{min-width:0;width:100%}
.dial-copy b{font-size:.92rem!important;line-height:1.25!important;overflow-wrap:anywhere!important;white-space:normal!important}
.dial-copy span{font-size:.75rem!important;line-height:1.3!important;color:#9eabbc!important;margin-top:.15rem!important}

/* Slider controls */
[data-testid="stSlider"]{padding:.15rem .25rem .35rem!important}
[data-testid="stSlider"] label p{font-size:.93rem!important;line-height:1.25!important}

/* Benchmark and flow grids */
.benchmark-grid{gap:.8rem!important}
.bench-card{min-width:0!important;overflow:hidden!important;padding:.9rem!important}
.bench-card strong{font-size:.9rem!important;line-height:1.25!important;overflow-wrap:anywhere!important}
.bench-card small{display:block;font-size:.76rem!important;line-height:1.35!important;overflow-wrap:anywhere!important}

/* System / endpoint cards */
.endpoint-grid{gap:.85rem!important}.endpoint-card{min-width:0!important;border:1.5px solid #334961!important;background:linear-gradient(180deg,#121b26,#0e1620)!important}.endpoint-meta{overflow-wrap:anywhere!important}.endpoint-card .mono{font-size:.82rem!important}

/* Buttons */
[data-testid="stButton"] button{background:linear-gradient(135deg,#0dbed6,#0788aa)!important;border:1px solid #1ee8ff!important;color:white!important;box-shadow:0 0 18px rgba(24,215,255,.16)!important;min-height:46px!important}
[data-testid="stButton"] button:hover{background:linear-gradient(135deg,#19ddf7,#0ba4c8)!important;box-shadow:0 0 24px rgba(24,215,255,.28)!important}

/* Information boxes */
[data-testid="stAlert"]{border-radius:14px!important;border-width:1.5px!important;background:#0e1824!important}

/* Chart / topology */
.topology-wrap,.frontier-card,.timeline{border:1.5px solid #334961!important;border-radius:18px!important;background:linear-gradient(180deg,#111a25,#0d151f)!important}

/* Prevent clipping across common custom cards */
.telemetry-card,.node-card,.flow-step,.proof-row,.pipe,.cache-card,.timeline,.topology-wrap,.story-step,.attack-card,.endpoint-card,.bench-card,.cap-card,.mini-step,.empty-state{box-sizing:border-box!important;min-width:0!important}
.telemetry-card *, .node-card *, .story-step *, .pipe *, .bench-card *, .attack-card *, .cache-card *{max-width:100%;box-sizing:border-box}

@media(max-width:1220px){
  .shard-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .telemetry-strip{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  .dial-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  .story-flow{grid-template-columns:repeat(3,minmax(0,1fr))!important}
  .story-step:not(:last-child):after{display:none!important}
  .benchmark-grid{grid-template-columns:repeat(3,minmax(0,1fr))!important}
}
@media(max-width:760px){
  .block-container{padding-left:1rem!important;padding-right:1rem!important}
  .shard-grid,.telemetry-strip,.dial-grid,.story-flow,.benchmark-grid,.endpoint-grid,.attack-grid{grid-template-columns:1fr!important}
  .node-kv{grid-template-columns:1fr auto}
}

/* V6.1 rendering hotfix + proof/attack presentation */
.proof-intro{display:flex;align-items:center;gap:.9rem;padding:1rem 1.1rem;margin:.55rem 0 1rem;border:1.5px solid rgba(182,108,255,.46);border-radius:16px;background:linear-gradient(135deg,rgba(182,108,255,.10),rgba(24,215,255,.05));box-shadow:0 0 24px rgba(182,108,255,.05)}
.proof-orb{width:42px;height:42px;min-width:42px;border-radius:50%;display:grid;place-items:center;border:2px solid var(--violet);color:#fff;background:rgba(182,108,255,.10);box-shadow:0 0 18px rgba(182,108,255,.22);font-weight:900}
.proof-intro strong{display:block;color:#f6f8ff;font-size:1rem}.proof-intro span{color:#bdc9d8;font-size:.92rem;line-height:1.45}
.shard-grid{align-items:stretch!important}
.node-card{height:auto!important;min-height:286px!important}
.node-headline{gap:.8rem!important}.status-pill{flex-shrink:0!important;white-space:nowrap!important}
.node-kv{gap:.7rem!important}.node-kv>span{min-width:0!important}.node-kv>b{min-width:0!important;overflow-wrap:anywhere!important}
@media(max-width:1050px){.shard-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}.node-card{min-height:250px!important}.mini-flow{grid-template-columns:1fr!important}.mini-step{min-height:auto!important}}
</style>
""",
    unsafe_allow_html=True,
)

if "view_mode" not in st.session_state:
    st.session_state.view_mode = "Explorer"
if "requester_prefix" not in st.session_state:
    st.session_state.requester_prefix = REQUESTER_PREFIX

with st.sidebar:
    st.markdown("## ◈ State Command")
    st.caption("Distributed state operations console · V6")
    st.markdown("""<div class='nav-guide'>
      <div><b>CLUSTER</b><span>Overview · World State</span></div>
      <div><b>EXECUTION</b><span>Block · Lookup · Cache</span></div>
      <div><b>VERIFICATION</b><span>Proofs · Attacks</span></div>
      <div><b>ANALYSIS</b><span>Performance</span></div>
      <div><b>SYSTEM</b><span>Registry · Activity</span></div>
    </div>""", unsafe_allow_html=True)
    page = st.radio(
        "Navigate",
        [
            "Overview",
            "World State",
            "Execute Block",
            "State Lookup",
            "Cache Inspector",
            "Proof Explorer",
            "Performance Lab",
            "Attack Lab",
            "System",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    st.session_state.view_mode = st.radio(
        "View mode",
        ["Explorer", "Protocol"],
        index=0 if st.session_state.view_mode == "Explorer" else 1,
        horizontal=True,
        help="Explorer uses human-friendly terminology. Protocol reveals raw IDs, hashes and proof data.",
    )
    st.session_state.requester_prefix = st.selectbox(
        "Requester",
        options=list(SHARDS),
        index=list(SHARDS).index(st.session_state.requester_prefix),
        format_func=lambda i: SHARDS[i]["name"],
        help="Choose which shard is asking for the state.",
    )
    st.divider()
    st.caption("Active node interface · GET /root · GET /state/{address}")

MODE = st.session_state.view_mode
REQUESTER = st.session_state.requester_prefix


# ---------------------------------------------------------------------------
# Reusable UI pieces
# ---------------------------------------------------------------------------
def render_header(title: str, subtitle: str):
    st.title(title)
    st.caption(subtitle)


def render_shard_identity(i: int, root: str | None = None):
    shard = SHARDS[i]
    st.subheader(shard["name"])
    st.caption(f"{shard['label']} storage region")
    if MODE == "Protocol":
        st.code(f"Protocol node: Node {i}\nOwnership prefix: {shard['prefix']}", language=None)
    if root:
        st.caption(f"State fingerprint: {short_hash(root)}")
        if MODE == "Protocol":
            with st.expander("Full state root"):
                st.code(root, language=None)


def render_network_cards():
    """Render the four live shards as a compact responsive command-centre grid."""
    statuses = {}
    cards = []
    for i in SHARDS:
        online, root, latency = check_node_online(NODE_ENDPOINTS[i])
        statuses[i] = {"online": online, "root": root, "latency": latency}
        shard = SHARDS[i]
        state = "ONLINE" if online else "OFFLINE"
        latency_text = f"{latency:.1f} ms" if latency is not None else "—"
        root_text = short_hash(root, 9, 5) if root else "Unavailable"
        role = "Requester" if i == REQUESTER else "Peer shard"
        selected = " selected" if i == REQUESTER else ""
        dot = "ok" if online else "bad"
        endpoint = NODE_ENDPOINTS[i].replace("http://", "")
        # Keep each card on a single HTML block. Leading indentation/newlines can be
        # interpreted by Markdown as a code block, which caused Shards B-D to render
        # as literal HTML in Streamlit.
        cards.append(
            f"<div class='node-card shard-{i} {status_class(online)}{selected}'>"
            f"<div class='node-headline'><div><div class='node-name'>{shard['name']}</div><div class='node-sub'>Node {i}</div></div>"
            f"<div class='status-pill'><span class='dot {dot}'></span>{state}</div></div>"
            f"<div class='node-kv'><span>Ownership prefix</span><b class='prefix-chip'>{shard['prefix']}</b></div>"
            f"<div class='node-kv'><span>Endpoint</span><b class='mono endpoint-text'>{endpoint}</b></div>"
            f"<div class='node-kv'><span>Root RTT</span><b>{latency_text}</b></div>"
            f"<div class='node-kv'><span>State fingerprint</span><b class='mono'>{root_text}</b></div>"
            f"<div class='node-kv'><span>Role</span><b>{role}</b></div></div>"
        )
    st.markdown("<div class='shard-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)

    if MODE == "Protocol":
        roots = [f"{SHARDS[i]['name']} · {statuses[i]['root']}" for i in SHARDS if statuses[i].get('root')]
        if roots:
            with st.expander("Inspect full shard roots"):
                st.code("\n".join(roots), language=None)
    return statuses

def render_telemetry(statuses: dict[int, dict[str, Any]]):
    healthy = sum(1 for s in statuses.values() if s["online"])
    latencies = [s["latency"] for s in statuses.values() if s["latency"] is not None]
    avg_rtt = f"{sum(latencies)/len(latencies):.1f} ms" if latencies else "Unavailable"
    roots = [s["root"] for s in statuses.values() if s["root"]]
    cluster_root = "Not anchored"  # No canonical global root exists in the current API.
    peers = f"{healthy}/4 reachable"
    st.markdown(
        f"""<div class='telemetry-strip'>
        <div class='telemetry-card'><div class='telemetry-label'>Canonical world root</div><div class='telemetry-value'>{cluster_root}</div></div>
        <div class='telemetry-card'><div class='telemetry-label'>Shard connectivity</div><div class='telemetry-value'>{peers}</div></div>
        <div class='telemetry-card'><div class='telemetry-label'>Average root RTT</div><div class='telemetry-value'>{avg_rtt}</div></div>
        <div class='telemetry-card'><div class='telemetry-label'>Transaction throughput</div><div class='telemetry-value'>Not exposed</div></div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_topology(statuses: dict[int, dict[str, Any]]):
    # The current demo uses direct static endpoint routing, not gossip or a DHT.
    points = {0:(130,75), 1:(390,75), 2:(130,235), 3:(390,235)}
    shard_colors = {0:'#18d7ff', 1:'#b66cff', 2:'#19f5a6', 3:'#ff4f7d'}
    lines = []
    for a,b in [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]:
        x1,y1=points[a]; x2,y2=points[b]
        lines.append(
            f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' "
            f"stroke='#13cfe8' stroke-width='4' stroke-linecap='round' opacity='.72' "
            f"style='filter:drop-shadow(0 0 6px rgba(0,231,255,.38))'/>"
        )
    nodes=[]
    for i,(x,y) in points.items():
        online=statuses[i]['online']
        accent=shard_colors[i]
        status_ring='#19f5a6' if online else '#ff536b'
        nodes.append(
            f"<circle cx='{x}' cy='{y}' r='47' fill='#111a25' stroke='{accent}' stroke-width='4' "
            f"style='filter:drop-shadow(0 0 8px {accent})'/>"
            f"<circle cx='{x+34}' cy='{y-34}' r='7' fill='{status_ring}' stroke='#0a0f16' stroke-width='3'/>"
            f"<text x='{x}' y='{y-3}' text-anchor='middle' fill='#f4f8ff' font-size='14' font-weight='800'>{SHARDS[i]['name']}</text>"
            f"<text x='{x}' y='{y+19}' text-anchor='middle' fill='#aeb9c8' font-size='11' font-weight='700'>{SHARDS[i]['prefix']}</text>"
        )
    svg=f"""<div class='topology-wrap'><svg viewBox='0 0 520 310' width='100%' role='img' aria-label='Direct static four shard topology'>{''.join(lines)}{''.join(nodes)}</svg></div>"""
    st.markdown(svg, unsafe_allow_html=True)
    st.caption("Direct owner routing between the four configured shards. The brighter lines show the available route map; Kademlia, gossip and devp2p are not part of this running prototype.")

def render_ownership_explanation(result: dict[str, Any]):
    owner = result["owner_prefix"]
    account_name = friendly_account_name(result["address"])
    st.markdown(f"### Why does {account_name} belong to {SHARDS[owner]['name']}?")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**1 · Account identifier**")
        st.write(account_name)
        if MODE == "Protocol":
            st.code(result["address"], language=None)
    with c2:
        st.markdown("**2 · Ownership hash**")
        st.write(short_hash(result["digest_hex"], 12, 8))
        if MODE == "Protocol":
            st.code(result["digest_hex"], language=None)
    with c3:
        st.markdown("**3 · Leading ownership bits**")
        st.metric("Selected bits", result["prefix_bits"])
        st.write(f"→ **{SHARDS[owner]['name']}**")

    if MODE == "Protocol":
        st.caption(f"First hash byte: {result['first_byte_binary']} · prefix length: {PREFIX_LENGTH} bits")


def render_query_result(result: dict[str, Any]):
    owner = result["owner_prefix"]
    requester = result["requester_prefix"]
    account_name = friendly_account_name(result["address"])

    top = st.columns([1.2, 1, 1, 1])
    top[0].metric("Account", account_name)
    top[1].metric("Home shard", SHARDS[owner]["name"])
    top[2].metric("Requester", SHARDS[requester]["name"])
    top[3].metric("Access", "Local" if result["local"] else "Cross-shard")

    render_ownership_explanation(result)
    st.divider()

    if result["local"]:
        st.success(f"{SHARDS[requester]['name']} owns this account. No remote state request is required.")
        st.info(
            "The current HTTP demo does not expose a local-state read endpoint through this dashboard path, "
            "so ownership can be proven here but the local value is not fetched by V2 yet."
        )
        return

    st.markdown("### Live request flow")
    steps = [
        ("Ownership resolved", f"{account_name} belongs to {SHARDS[owner]['name']}."),
        ("Remote state requested", f"{SHARDS[requester]['name']} asks {SHARDS[owner]['name']} for the account state."),
        ("State + Merkle proof received", f"{result['proof_nodes']} proof nodes · {human_bytes(result['proof_bytes'])}."),
        ("State fingerprint fetched", "A separate /root request retrieves the owner shard's current root."),
        ("Merkle proof verified", "The proof is evaluated locally against that root."),
    ]
    flow_html = "<div class='flow-grid'>" + "".join(
        f"<div class='flow-step'><b><span class='pass'>●</span> {label}</b><span class='small-muted'>{text}</span></div>"
        for label, text in steps
    ) + "</div>"
    st.markdown(flow_html, unsafe_allow_html=True)

    if result["accepted"]:
        st.success(f"Verified state accepted · {result['proof_value_text']}")
    else:
        st.error("State rejected because one or more verification checks failed.")

    st.markdown("### Verification checks")
    checks = st.columns(3)
    checks[0].metric("Root consistency", "PASS" if result["root_match"] else "FAIL")
    checks[1].metric("MPT proof", "PASS" if result["proof_ok"] else "FAIL")
    checks[2].metric("Claimed value", "PASS" if result["claim_match"] else "FAIL")

    st.markdown("### Live performance")
    perf = st.columns(4)
    perf[0].metric("State request", f"{result['state_request_ms']:.2f} ms")
    perf[1].metric("Root request", f"{result['root_request_ms']:.2f} ms")
    perf[2].metric("Proof verify", f"{result['verify_ms']:.2f} ms")
    perf[3].metric("Proof payload", human_bytes(result["proof_bytes"]))

    if MODE == "Protocol":
        st.markdown("### Protocol trace")
        st.code(
            "\n".join(
                [
                    f"requester_node = {requester}",
                    f"owner_node = {owner}",
                    f"ownership_prefix = {result['prefix_bits']}",
                    f"GET {NODE_ENDPOINTS[owner]}/state/{result['address']}",
                    f"GET {NODE_ENDPOINTS[owner]}/root",
                    f"wire_root = {result['wire'].get('root')}",
                    f"fetched_root = {result['trusted_root']}",
                    f"root_match = {result['root_match']}",
                    f"proof_ok = {result['proof_ok']}",
                    f"claim_match = {result['claim_match']}",
                    f"accepted = {result['accepted']}",
                ]
            ),
            language=None,
        )


def render_proof_path(result: dict[str, Any]):
    proof = result.get("wire", {}).get("proof", [])
    if not proof:
        st.caption("No proof nodes available.")
        return
    rows=[]
    for idx, entry in enumerate(proof, start=1):
        try:
            raw = base64.b64decode(entry.get("value", ""))
            digest = hashlib.sha256(raw).hexdigest()
            size = len(raw)
        except Exception:
            digest = "unavailable"
            size = 0
        rows.append(f"<div class='proof-row'><div class='proof-index'>{idx}</div><div class='proof-body'><div class='proof-title'>Witness node {idx} · {human_bytes(size)}</div><div class='proof-hash'>local display fingerprint: {digest[:18]}…{digest[-10:] if digest != 'unavailable' else ''}</div></div></div>")
    st.markdown("".join(rows), unsafe_allow_html=True)
    st.caption("These fingerprints are UI aids for distinguishing proof nodes; verification itself still uses the decoded MPT witness against the returned trie root.")


def get_last_remote_result():
    result = st.session_state.get("last_result")
    if not result or result.get("local"):
        return None
    return result



def render_dials(items):
    """Presentation dials; the native sliders above remain the actual controls."""
    parts=["<div class='dial-grid'>"]
    for label,value,minv,maxv,unit,accent in items:
        pct=0 if maxv==minv else max(0,min(100,(float(value)-minv)/(maxv-minv)*100))
        display=f"{value}{unit}"
        parts.append(
            f"<div class='dial-card'>"
            f"<div class='dial' style='--pct:{pct:.1f};--accent:{accent}'><div class='dial-value'>{display}</div></div>"
            f"<div class='dial-copy'><b>{label}</b><span>Current setting</span></div>"
            f"</div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)

def render_coverage_chart():
    rows=[]
    for depth in range(7):
        frac=2**(-depth)*100
        rows.append(f"<div class='coverage-row'><b>p = {depth}</b><div class='coverage-track'><div class='coverage-fill' style='width:{frac:.3f}%'></div></div><span>{frac:.3f}% local</span></div>")
    st.markdown("<div class='coverage-list'>"+"".join(rows)+"</div>", unsafe_allow_html=True)

def render_empty_flow(title,subtitle,steps):
    html=f"<div class='empty-state'><div class='empty-title'>{title}</div><div class='empty-sub'>{subtitle}</div><div class='mini-flow'>"
    for i,(head,body) in enumerate(steps,1):
        html+=f"<div class='mini-step'><b>{i} · {head}</b><div class='small-muted'>{body}</div></div>"
    html+='</div></div>'
    st.markdown(html,unsafe_allow_html=True)


def render_story_flow(steps):
    html="<div class='story-flow'>"
    for i,(title,desc) in enumerate(steps,1):
        html += f"<div class='story-step'><div class='snum'>STEP {i}</div><div class='stitle'>{title}</div><div class='sdesc'>{desc}</div></div>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

def render_semantic_legend():
    st.markdown("""<div class='legend-row'><span class='legend-chip cyan'>CYAN · routing</span><span class='legend-chip mint'>MINT · verified / healthy</span><span class='legend-chip coral'>CORAL · failure / rejection</span><span class='legend-chip violet'>PURPLE · modelled</span><span class='legend-chip amber'>AMBER · emulated</span></div>""", unsafe_allow_html=True)

def render_frontier_chart(current_p:int):
    # Honest visualization: deterministic storage saved + normalized remote-dependency index.
    xs=[45,145,245,345,445,545,645]
    saved=[0,50,75,87.5,93.75,96.875,98.4375]
    remote=[0,50,75,87.5,93.75,96.875,98.4375]
    def y(v): return 215 - (v/100)*165
    pts_saved=' '.join(f"{x},{y(v):.1f}" for x,v in zip(xs,saved))
    pts_remote=' '.join(f"{x},{y(v):.1f}" for x,v in zip(xs,remote))
    marker_x=xs[current_p]
    marker_y=y(saved[current_p])
    labels=''.join(f"<text x=\'{x}\' y=\'242\' text-anchor=\'middle\' fill=\'#aeb9c8\' font-size=\'11\'>p={i}</text>" for i,x in enumerate(xs))
    svg=f"""<div class='frontier-card'><div class='frontier-head'><div><div class='frontier-title'>Storage–network dependency frontier</div><div class='frontier-sub'>Storage saved is deterministic from 2^-p. Remote dependency is shown as a normalized index, not fabricated MB.</div></div><div class='frontier-legend'><span class='g'>● Storage saved</span> &nbsp; <span class='c'>● Remote dependency</span></div></div><svg viewBox='0 0 700 260' width='100%' role='img' aria-label='Storage saved and relative remote dependency by prefix depth'><line x1='45' y1='215' x2='655' y2='215' stroke='#34475f' stroke-width='1.5'/><line x1='45' y1='50' x2='45' y2='215' stroke='#34475f' stroke-width='1.5'/><polyline points='{pts_saved}' fill='none' stroke='#19f5a6' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/><polyline points='{pts_remote}' fill='none' stroke='#00e7ff' stroke-width='2.5' stroke-dasharray='8 7' stroke-linecap='round'/><line x1='{marker_x}' y1='50' x2='{marker_x}' y2='215' stroke='#b47cff' stroke-width='2' opacity='.72'/><circle cx='{marker_x}' cy='{marker_y:.1f}' r='7' fill='#b47cff' stroke='#f7fbff' stroke-width='2'/>{labels}<text x='18' y='56' fill='#aeb9c8' font-size='10'>100</text><text x='25' y='218' fill='#aeb9c8' font-size='10'>0</text></svg></div>"""
    st.markdown(svg, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
if page == "Overview":
    render_header(
        "Ethereum Distributed State Lab",
        "A human-friendly control centre for the live four-shard state-sharing network.",
    )
    st.markdown("<div class='command-kicker'>Live distributed state network</div>", unsafe_allow_html=True)
    statuses = render_network_cards()
    render_telemetry(statuses)

    healthy = sum(1 for s in statuses.values() if s["online"])
    st.markdown("### Network topology")
    render_topology(statuses)

    st.markdown("### Network at a glance")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Storage shards", "4")
    m2.metric("Healthy", f"{healthy}/4")
    m3.metric("Ownership rule", "SHA-256 → 2 bits")
    m4.metric("Proof structure", "Merkle Patricia Trie")
    render_semantic_legend()

    if healthy < 4:
        st.warning("The console is ready, but one or more local shard processes are offline. Start the four-node Docker network for the full live demonstration.")

    st.markdown("### How a remote state lookup works")
    render_story_flow([
        ("Account", "Start with the requested account identifier."),
        ("Find owner", "SHA-256 determines the two ownership bits."),
        ("Fetch state", "The requester contacts the responsible shard."),
        ("Verify proof", "The returned MPT witness is checked locally."),
        ("Accept / reject", "Only cryptographically consistent state is accepted."),
    ])

    st.markdown("### Working proof of concept")
    st.markdown("<div class='cap-grid'>"
                "<div class='cap-card'><div class='cap-title'>01 · Deterministic ownership</div><p>The visible address itself does not reveal the shard. We hash the address first, then use the leading two hash bits.</p></div>"
                "<div class='cap-card'><div class='cap-title'>02 · Direct cross-shard retrieval</div><p>If the requester does not own the account, it contacts the configured owner shard and requests the state plus witness.</p></div>"
                "<div class='cap-card'><div class='cap-title'>03 · Independent verification</div><p>The requester reconstructs the value from the Merkle-Patricia-Trie proof instead of blindly trusting the peer's raw claim.</p></div>"
                "</div>", unsafe_allow_html=True)

    with st.expander("Behind the scenes · protocol details and research extensions"):
        st.markdown("**Current routing:** direct owner lookup using the static shard endpoint map.  \n**Current trust boundary:** the owner shard supplies both the witness and its root; a later phase will anchor shard roots to an independently trusted global commitment.  \n**Research execution path:** block-level state resolution → value cache → speculative prefetch → parallel verification → runtime fallback → commit.")
        st.markdown("<div class='cap-grid'>"
                    "<div class='cap-card'><div class='cap-title'>Block-centred execution</div><p>Resolve likely block state before EVM execution while preserving a runtime miss path for dynamically discovered storage.</p></div>"
                    "<div class='cap-card'><div class='cap-title'>Cache + speculative prefetch</div><p>Reuse hot values and move remote state waits away from the EVM critical path wherever possible.</p></div>"
                    "<div class='cap-card'><div class='cap-title'>Proof Trie Dictionary</div><p>Merge repeated MPT node preimages across a block witness so shared trie nodes are transmitted once.</p></div>"
                    "</div>", unsafe_allow_html=True)
        st.caption("Detailed activity is available under System → Activity trace.")


elif page == "World State":
    render_header(
        "World State",
        "Explore how prefix depth changes local state coverage and the storage–bandwidth frontier.",
    )
    st.markdown(f"Running prototype uses **p = {PREFIX_LENGTH}** {badge('LIVE','live')}", unsafe_allow_html=True)
    render_story_flow([("Increase p","Use more ownership bits."),("Store less locally","Each deeper bit halves local key-space coverage."),("Fetch more remotely","A larger fraction of execution state may live on peer shards."),("Trade disk for network","The experiment studies where that balance becomes worthwhile."),("Benchmark","Later replace the relative model with measured bytes and latency.")])
    p = st.slider("Adjust logical prefix depth", min_value=0, max_value=6, value=2, step=1,
                  help="This control does not reconfigure the running nodes. It models the fraction 2^-p stored locally.")
    fraction = 2 ** (-p) if p >= 0 else 1.0
    render_dials([("Prefix depth",p,0,6,"", "#33d6ff"),("Local state",round(fraction*100,1),0,100,"%", "#7ee787"),("Storage saved",round((1-fraction)*100,1),0,100,"%", "#c084fc"),("Physical shards",4,0,4,"", "#ff5c63")])
    saved = (1-fraction)*100
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Effective prefix p", str(p))
    c2.metric("Expected local state", f"{fraction*100:.2f}%")
    c3.metric("Modelled storage saved", f"{saved:.2f}%")
    c4.metric("Physical live shards", "4")
    st.caption("For p > 2, This view folds logical prefixes onto the existing four physical demo nodes for visualization only; the Go network is still configured at p=2.")
    st.markdown("### Trie coverage model")
    st.caption("Each deeper binary prefix halves the fraction of the global key space a node is expected to retain locally.")
    render_coverage_chart()
    st.markdown("### Storage–network dependency frontier")
    render_frontier_chart(p)
    st.info("The green curve is derived directly from 2^-p. The cyan curve is a relative remote-dependency index only; we will replace it with measured network bytes once execution instrumentation is connected.")
    st.markdown("### Current physical network")
    statuses=render_network_cards(); render_telemetry(statuses)

elif page == "Execute Block":
    render_header(
        "Execute Demo Block",
        "A labelled research simulator for K(B) resolution, LFU cache use, speculative prefetch, runtime fallback, proof deduplication and execution timing.",
    )
    st.warning("This page is a labelled research model. Ownership hashing is deterministic; block timing, cache outcomes and witness-node accounting remain modelled until the execution runtime exposes measured telemetry.")
    render_story_flow([("State needed","Estimate the block's likely state demand."),("Resolve locally","Use owned state first."),("Reuse cache","Serve hot values without another network request."),("Prefetch + verify","Fetch likely misses concurrently and verify witnesses."),("Execute safely","Run the EVM with runtime fallback for dynamic misses.")])
    a,b,c,d=st.columns(4)
    tx_count=a.select_slider("Transactions",options=[25,50,100,250,500],value=50)
    p=b.slider("Logical prefix depth",0,6,2)
    rtt=c.slider("Emulated RTT / remote batch",0,250,40,10)
    cap=d.select_slider("LFU capacity / tier",options=[16,32,64,128,256],value=64)
    render_dials([("Transactions",tx_count,25,500,"", "#33d6ff"),("Prefix depth",p,0,6,"", "#7ee787"),("Emulated RTT",rtt,0,250," ms", "#ffb454"),("LFU capacity",cap,16,256,"", "#c084fc")])
    if st.button("▶ Execute Demo Block",type="primary",use_container_width=True):
        with st.spinner("Resolving block state requirements…"):
            st.session_state.last_demo_block=run_demo_block(tx_count,p,REQUESTER,rtt,cap)
            log_event("info",f"Modelled block executed: {tx_count} tx · p={p} · emulated RTT={rtt} ms")
    res=st.session_state.get("last_demo_block")
    if res:
        st.markdown("### What happened in this modelled block")
        st.caption("Read left to right: total state demand → local coverage → cache reuse → remote prefetch → proof work → dynamic fallback → commit hook.")
        st.markdown("### Resolution pipeline")
        pipeline=[
            ("1","K(B)",f"{res['unique_keys']} unique keys"),
            ("2","Local",f"{len(res['local'])} keys"),
            ("3","LFU cache",f"{len(res['cache_hits'])} hits"),
            ("4","Prefetch",f"{len(res['prefetched'])} remote hints"),
            ("5","Verify",f"{res['unique_nodes']} unique nodes"),
            ("6","EVM + fallback",f"{len(res['fallback'])} dynamic misses"),
            ("7","Commit","new-root hook pending"),
        ]
        st.markdown("<div class='pipeline'>"+"".join(f"<div class='pipe'><div class='num'>{n}</div><div class='title'>{t}</div><div class='desc'>{x}</div></div>" for n,t,x in pipeline)+"</div>",unsafe_allow_html=True)
        m=st.columns(6)
        m[0].metric("State keys",res['unique_keys'])
        m[1].metric("Local",len(res['local']))
        m[2].metric("Cache hits",len(res['cache_hits']))
        m[3].metric("Remote",len(res['remote']))
        m[4].metric("Prefetch coverage", "N/A" if len(res['remote']) == 0 else f"{res['prefetch_coverage']:.1f}%")
        m[5].metric("Fallback misses",len(res['fallback']))
        st.markdown("### Block execution timeline")
        stages=[("State resolution",max(.1,res['fetch_ms'])),("Parallel proof verify",max(.1,res['verify_ms_model'])),("EVM CPU",max(.1,res['evm_cpu_ms_model'])),("Dynamic fallback",max(.1,res['fallback_penalty_ms']))]
        maxv=max(x for _,x in stages)
        html="<div class='timeline'>"
        for name,val in stages:
            width=min(100,val/maxv*100 if maxv else 0)
            html+=f"<div class='tl-row'><span>{name}</span><div class='tl-track'><div class='tl-fill' style='width:{width:.1f}%'></div></div><b>{val:.1f} ms</b></div>"
        html+="</div>"
        st.markdown(html,unsafe_allow_html=True)
        st.caption("RTT and end-to-end latency are EMULATED/MODELLED; they are not localhost measurements.")
        st.markdown("### Proof Trie Dictionary model")
        q=st.columns(4)
        q[0].metric("Individual witness nodes",res['individual_nodes'])
        q[1].metric("Unique dictionary nodes",res['unique_nodes'])
        q[2].metric("Individual proof bytes",human_bytes(res['individual_proof_bytes']))
        q[3].metric("Dictionary bytes",human_bytes(res['dictionary_proof_bytes']))
        st.metric("Modelled witness reduction",f"{res['proof_saving_pct']:.1f}%")
        st.caption("Proof-node IDs and byte accounting on this page are deterministic models, not decoded live MPT nodes. The Proof Explorer remains the cryptographic ground truth for individual live lookups.")

elif page == "Cache Inspector":
    render_header("Cache Inspector","Inspect the three LFU value-cache domains used by the execution research model.")
    cache=ensure_demo_cache()
    res=st.session_state.get("last_demo_block")
    if not res:
        st.info("Run Execute Demo Block once to populate the modelled LFU caches.")
    tier_help={'Account Headers':'Balance, nonce, storage root and code hash.','Storage Slots':'Individual contract variables and sparse state.','Contract Bytecode':'Executable contract code that may be reused repeatedly.'}
    cards=[]
    for tier in CACHE_TIERS:
        pool=cache[tier]
        hits=(res['tier_hits'][tier] if res else 0); misses=(res['tier_misses'][tier] if res else 0)
        rate=hits/(hits+misses)*100 if hits+misses else 0
        cards.append(f"<div class='cache-card'><b>{tier}</b><div class='cache-explain'>{tier_help.get(tier,'Cached state values.')}</div><div class='cache-bar'><div class='cache-fill' style='width:{rate:.1f}%'></div></div><div class='small-muted'>Hit rate {rate:.1f}% · {len(pool)} entries · {sum(v['bytes'] for v in pool.values())/1024:.1f} KB</div></div>")
    st.markdown("<div class='cache-grid'>"+"".join(cards)+"</div>",unsafe_allow_html=True)
    if res:
        st.markdown("### Current block cache telemetry")
        cols=st.columns(3)
        for col,tier in zip(cols,CACHE_TIERS):
            hits=res['tier_hits'][tier]; misses=res['tier_misses'][tier]; ev=res['evictions'][tier]; entries=len(cache[tier])
            with col:
                st.metric(tier, f"{hits} hits")
                st.caption(f"{misses} misses · {ev} evictions · {entries} entries")
    if st.button("Reset modelled cache"):
        st.session_state.demo_cache={tier:{} for tier in CACHE_TIERS}; st.success("Modelled LFU cache cleared.")
    st.info("Why cache? Repeated state requests can be served locally instead of fetched from another shard again. Value caching and proof caching are intentionally separated because MPT witnesses are tied to a specific state root.")

elif page == "Performance Lab":
    render_header("Performance Lab","Compare the same workload as storage is partitioned and each optimization is added one stage at a time.")
    render_story_flow([("Full state","Baseline: maximum local storage, minimum remote dependency."),("Prefix only","Reduce disk use and expose remote-state cost."),("+ Cache","Avoid repeated remote retrievals."),("+ Prefetch","Move likely network waits before execution."),("+ Proof dictionary","Reduce repeated witness-node bytes.")])
    tx_count=st.select_slider("Benchmark workload",options=[25,50,100,250,500],value=100)
    p=st.slider("Benchmark logical prefix depth",0,6,2,key="perf_p")
    rtt=st.slider("Emulated WAN RTT",0,250,50,10,key="perf_rtt")
    render_dials([("Workload",tx_count,25,500," tx", "#33d6ff"),("Prefix depth",p,0,6,"", "#7ee787"),("Emulated WAN RTT",rtt,0,250," ms", "#ffb454")])
    if st.button("Run comparative benchmark",type="primary"):
        # Fresh-cache deterministic comparisons to keep workload symmetry.
        saved=st.session_state.get("demo_cache")
        rows=[]
        for name,cache_cap,prefetch,batch in [
            ("Full State",256,False,False),("Prefix Only",1,False,False),("+ LFU",64,False,False),("+ Prefetch",64,True,False),("+ Proof Dictionary",64,True,True)]:
            st.session_state.demo_cache={tier:{} for tier in CACHE_TIERS}
            rr=run_demo_block(tx_count,0 if name=="Full State" else p,REQUESTER,rtt,cache_cap)
            remote=len(rr['remote'])
            fallback=0 if prefetch else remote
            coverage=rr['prefetch_coverage'] if prefetch else 0
            proof_bytes=rr['dictionary_proof_bytes'] if batch else rr['individual_proof_bytes']
            total=rr['evm_cpu_ms_model'] + (rr['fetch_ms'] if name!="Full State" else 0) + (rr['verify_ms_model'] if name!="Full State" else 0) + (rtt*fallback)
            rows.append({"Configuration":name,"Storage fraction":f"{(2**(-(0 if name=='Full State' else p)))*100:.2f}%","Remote keys":remote,"Cache hits":len(rr['cache_hits']),"Prefetch coverage":f"{coverage:.1f}%","Fallback misses":fallback,"Proof bytes":proof_bytes,"Modelled total ms":round(total,1)})
        st.session_state.demo_cache=saved if saved is not None else {tier:{} for tier in CACHE_TIERS}
        st.session_state.perf_rows=rows
    rows=st.session_state.get("perf_rows")
    if rows:
        st.markdown("### Comparative latency")
        df=pd.DataFrame(rows)
        chart_df=df.set_index("Configuration")[["Modelled total ms"]]
        st.bar_chart(chart_df)
        cards="<div class='benchmark-grid'>"
        for row in rows:
            cards+=f"<div class='bench-card'><strong>{row['Configuration']}</strong><div class='big'>{row['Modelled total ms']:.1f} ms</div><small>{row['Storage fraction']} stored · {row['Remote keys']} remote · {row['Fallback misses']} fallback</small></div>"
        cards+='</div>'
        st.markdown(cards,unsafe_allow_html=True)
        st.warning("All benchmark values on this page are MODELLED. They define the measurement plan until live execution instrumentation is connected.")
        st.markdown("### Metrics planned for live instrumentation")
        st.markdown("Storage bytes · remote requests · cache hits/misses/evictions · fetched bytes · proof bytes · prefetch coverage · dynamic fallback misses · proof verification CPU · EVM CPU · end-to-end block latency")

elif page == "State Lookup":
    render_header(
        "State Lookup",
        "Find an account's home shard, retrieve remote state when needed, and verify it cryptographically.",
    )

    render_story_flow([("Enter account","Choose the state item to inspect."),("Hash address","SHA-256 produces the ownership digest."),("Resolve shard","The first two digest bits select A/B/C/D."),("Retrieve + verify","Remote state is returned with an MPT witness."),("Accept / reject","The local verifier decides whether the state is trustworthy.")])
    address = st.text_input(
        "Account ID",
        value=st.session_state.get("address_input", DEFAULT_ADDRESS),
        help="Explorer mode calls this an Account ID; Protocol mode reveals the raw Ethereum-style address.",
    )
    st.session_state.address_input = address

    if st.button("Find & Verify State", type="primary", use_container_width=True):
        try:
            with st.spinner("Running live ownership and proof checks…"):
                st.session_state.last_result = run_state_query(address, REQUESTER)
        except Exception as exc:
            st.session_state.last_result = None
            st.error(f"State lookup could not complete: {exc}")
            st.caption("If the error says connection refused, the ownership calculation still succeeded; the responsible local shard process simply was not reachable.")

    result = st.session_state.get("last_result")
    if result:
        render_query_result(result)
    else:
        st.info("Enter an account ID and start a lookup. The dashboard will show every decision it makes.")


elif page == "Proof Explorer":
    render_header(
        "Proof Explorer",
        "Inspect the actual Merkle-Patricia-Trie witness returned by the live owner shard.",
    )
    st.markdown("<div class='proof-intro'><div class='proof-orb'>π</div><div><strong>Cryptographic witness inspector</strong><span>Load one successful cross-shard lookup, then inspect the exact proof path, encoded bytes and verification timing without exposing raw protocol detail until you ask for it.</span></div></div>", unsafe_allow_html=True)
    result = get_last_remote_result()
    if result is None:
        render_empty_flow("No proof loaded yet","Proof Explorer becomes active after a successful remote state lookup.",[("Open State Lookup","Use the default account or enter another account ID."),("Run Find & Verify","The requester resolves ownership and retrieves state + witness from the owner shard."),("Return here","The real MPT proof path, byte size and verification timing will appear here.")])
    else:
        st.success(f"Loaded proof for {friendly_account_name(result['address'])} from {SHARDS[result['owner_prefix']]['name']}.")
        a, b, c = st.columns(3)
        a.metric("Proof nodes", result["proof_nodes"])
        b.metric("Encoded proof size", human_bytes(result["proof_bytes"]))
        c.metric("Verification time", f"{result['verify_ms']:.3f} ms")

        st.markdown("### Verification relationship")
        st.code(
            f"Requested account\n    ↓\nMPT proof ({result['proof_nodes']} nodes)\n    ↓\nCalculated membership under root\n    ↓\n{short_hash(result['trusted_root'])}\n    ↓\n{'VERIFIED' if result['proof_ok'] else 'REJECTED'}",
            language=None,
        )

        st.markdown("### Witness path")
        render_proof_path(result)

        st.markdown("### Proof-derived state")
        st.success(result["proof_value_text"] if result["proof_ok"] else "No verified value")

        with st.expander("View raw proof nodes"):
            proof = result["wire"].get("proof", [])
            for idx, entry in enumerate(proof, start=1):
                st.markdown(f"**Proof node {idx}**")
                st.code(entry.get("value", ""), language=None)

        with st.expander("View decoded RLP structure"):
            if result["decoded_nodes"]:
                for idx, node in enumerate(result["decoded_nodes"], start=1):
                    st.markdown(f"**Decoded node {idx}**")
                    st.code(repr(node), language=None)
            else:
                st.caption("Decoded proof nodes are unavailable because verification failed.")

        st.warning(
            "Security note: this proof is mathematically checked against the root returned by the owner shard. "
            "The next protocol phase will anchor that shard root to an independently trusted global commitment."
        )


elif page == "Attack Lab":
    render_header(
        "Attack Lab",
        "See exactly which kinds of tampering the current proof system detects—and which trust assumption still remains.",
    )
    st.markdown("<div class='attack-grid'><div class='attack-card'><span class='risk'>Payload attack</span><b>Change the claimed value</b><p>Tests whether a peer can lie about the returned account state.</p></div><div class='attack-card'><span class='risk'>Witness attack</span><b>Corrupt the Merkle proof</b><p>Flips proof data and checks whether cryptographic verification rejects it.</p></div><div class='attack-card'><span class='risk'>Trust-boundary attack</span><b>Replace the root</b><p>Shows why a proof is only meaningful when its anchor root is independently trusted.</p></div></div>", unsafe_allow_html=True)
    result = get_last_remote_result()
    if result is None:
        render_empty_flow("Attack Lab is waiting for a verified proof","The lab deliberately modifies a real witness rather than using fabricated attack data.",[("Fetch a remote account","Run a cross-shard State Lookup first."),("Load its witness","A successful lookup stores the latest proof in the session."),("Return here","Choose proof corruption, claimed-value tampering, or inspect the remaining root-trust assumption.")])
    else:
        attack = st.radio(
            "Choose an attack",
            ["Corrupt the Merkle proof", "Change the claimed value", "Explain the root-trust attack"],
        )

        if attack == "Corrupt the Merkle proof":
            st.write("One byte in the real returned witness will be flipped, then the same verifier will run again.")
            if st.button("Run proof corruption attack", type="primary"):
                bad_proof = tamper_proof(result["wire"].get("proof", []))
                ok, value, err, elapsed_ms, _ = verify_proof(result["trusted_root"], result["address"], bad_proof)
                if ok:
                    st.error("Unexpected result: the modified proof still verified.")
                    st.code(safe_decode_value(value), language=None)
                else:
                    st.success("Attack detected · state rejected")
                    st.metric("Failed verification time", f"{elapsed_ms:.3f} ms")
                    if MODE == "Protocol":
                        st.code(err or "verification failed", language=None)

        elif attack == "Change the claimed value":
            real_value = result["proof_value_text"]
            fake_value = st.text_input("Malicious claimed value", value="500 ETH")
            st.markdown("**Proof-derived value**")
            st.success(real_value)
            st.markdown("**Remote node claims**")
            st.error(fake_value)
            if st.button("Compare claim with verified proof", type="primary"):
                if fake_value == real_value:
                    st.warning("The chosen fake claim happens to equal the verified value. Change it and try again.")
                else:
                    st.success("Attack detected · claimed value does not match the proof-derived value")
                    st.caption("A hardened client should use the proof-derived value, not blindly trust the raw claimed field.")

        else:
            st.error("Current trust gap: the state proof and the 'trusted' root can both originate from the same remote shard.")
            st.code(
                "Malicious shard\n    ├─ returns fabricated state\n    ├─ returns valid proof for its fabricated trie\n    └─ returns the matching fabricated root\n\nCurrent verifier: proof is internally consistent\nDesired verifier: shard root must also be authenticated by a canonical global commitment",
                language=None,
            )
            st.info("This is the first protocol-side hardening step planned after the current proof-of-concept.")


elif page == "Research Lab":
    render_header(
        "Research Lab",
        "The measurable extension: authenticated shard roots first, then batched proof-node deduplication.",
    )

    st.markdown("### Proposed contribution")
    st.info(
        "**Authenticated Batched Cross-Shard State Retrieval**: verify a remote shard's state against an independently anchored "
        "global commitment, then reduce repeated witness data when several accounts are requested from the same shard."
    )

    st.markdown("### Phase 1 · Security correctness")
    st.code(
        "account state\n   ↓\nMPT membership proof\n   ↓\nshard root\n   ↓\nshard-root commitment proof\n   ↓\ncanonical global root\n   ↓\nACCEPT",
        language=None,
    )

    st.markdown("### Phase 2 · Proof efficiency benchmark")
    c1, c2 = st.columns(2)
    c1.markdown("**Baseline**")
    c1.code("N accounts\nN requests\nN separate proofs\nRepeated shared trie nodes", language=None)
    c2.markdown("**Optimized**")
    c2.code("N accounts\n1 batch request\n1 deduplicated witness\nEach shared trie node sent once", language=None)

    st.markdown("### Metrics to record")
    metric_cols = st.columns(4)
    metric_cols[0].metric("Proof bytes", "Awaiting instrumentation")
    metric_cols[1].metric("Unique nodes", "Awaiting instrumentation")
    metric_cols[2].metric("HTTP calls", "Awaiting instrumentation")
    metric_cols[3].metric("Verification time", "Awaiting instrumentation")

    st.warning(
        "These benchmark values are intentionally not fabricated. The batch witness interface must be implemented before this page can report measured comparison numbers."
    )


elif page == "System":
    render_header(
        "System",
        "Node connectivity, runtime interfaces, activity traces, and protocol instrumentation boundaries.",
    )
    statuses = render_network_cards()

    st.markdown("### Node registry")
    cards="<div class='endpoint-grid'>"
    for i in SHARDS:
        status="ONLINE" if statuses[i]["online"] else "OFFLINE"
        scolor="#7ee787" if statuses[i]["online"] else "#ff5c63"
        cards+=f"<div class='endpoint-card'><span class='endpoint-status' style='color:{scolor}'>{status}</span><b>{SHARDS[i]['name']} · Node {i}</b><div class='endpoint-meta'>Ownership prefix <span class='mono'>{SHARDS[i]['prefix']}</span><br><span class='mono'>{NODE_ENDPOINTS[i]}</span></div></div>"
    cards+='</div>'
    st.markdown(cards,unsafe_allow_html=True)

    st.markdown("### Activity trace")
    render_event_console(limit=24)

    st.markdown("### Active protocol interface")
    st.code("GET /root\nGET /state/{address}", language=None)

    st.markdown("### Planned protocol instrumentation")
    st.markdown(
        """
- `GET /status` telemetry: node identity, prefix depth, root, counters, measured RTT/request statistics.
- `GET /cache/stats` for the three value-cache domains and eviction counters.
- `POST /block/execute` or an equivalent benchmark hook returning state-resolution and execution timing.
- `POST /state/batch` returning a block-scoped Proof Trie Dictionary (unique MPT node preimages + requested keys + parent root).
- Canonical/global commitment endpoint or block-state object.
- Shard-root commitment proof generation and verification.
- Structured account values (nonce, balance, storageRoot, codeHash) rather than only opaque demo values.
- State-transition/transaction endpoint for a real before → after root update.
- Batch state-proof endpoint that can deduplicate shared proof nodes.
- Optional state-history endpoint for replaying prior roots/versions.
"""
    )

