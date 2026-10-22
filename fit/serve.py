"""Stage 8: the same bundle, served.

Stage 6 runs the delivery bundle as a batch job: submit, queue, poll, download.
This runs the *same bundle* with `mode: "serving"` instead, and asks it questions
directly -- once over HTTP with /query, then over a websocket with /connect.

    python fit/serve.py                       # bundle from fit/out/delivery/runs.json
    python fit/serve.py --bundle bdl_...      # or name one
    python fit/serve.py --windows 5           # how many windows to send (default 3)
    python fit/serve.py --keep                # leave the agent running

The windows are real: rows straight out of the delivery role files Stage 6 runs
over, 512 at a time.

Serving is not finished. The platform accepts the calls, keeps a real agent row
and enforces the real auth, but no runtime is placed and no model runs: the answer
is the request echoed back. What this stage shows is the shape of the path -- that
a delivered bundle becomes an addressable agent by changing one field, and that the
~40 s batch round trip becomes a call you wait on.
"""
import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import agents, api_base, load_dotenv, request          # noqa: E402

try:
    from websocket import create_connection                        # websocket-client
except ImportError:
    sys.exit("websocket-client is not installed: pip install -r requirements.txt")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "fit", "out", "delivery", "runs.json")

# A window as the agent sees one: 2.56 s at 200 Hz, so 512 rows of the nine
# z-scored channels. Stage 6 feeds the agent whole role files through a `file`
# connector; this sends the same rows, a window at a time.
CHANNELS = [f"{s}.{a}" for s in ("back", "side", "top") for a in "xyz"]
WINDOW_ROWS = 512
ROLES = os.path.join(ROOT, "data", "roles")


def default_role_file():
    """The first delivery file Stage 6 would have run over, if the roles are built."""
    delivery = os.path.join(ROLES, "delivery")
    if not os.path.isdir(delivery):
        return None
    names = sorted(n for n in os.listdir(delivery) if n.endswith(".csv"))
    return os.path.join(delivery, names[0]) if names else None


def read_windows(path, count, rows_per_window):
    """`count` windows of real rows, read from a role CSV."""
    windows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in CHANNELS if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"{path} is missing channels: {missing}")
        current = []
        for row in reader:
            current.append(row)
            if len(current) == rows_per_window:
                windows.append(current)
                current = []
                if len(windows) == count:
                    break
    if len(windows) < count:
        sys.exit(f"{path} holds only {len(windows)} full window(s) of {rows_per_window} rows")
    return windows


def payload(index, rows):
    """One window on the wire. Columnar, because 512x9 as records is mostly key names.

    The shape is this stage's own: a serving agent's input belongs to its
    blueprint's connectors, and that contract is not settled yet.
    """
    return {
        "window_index": index,
        "start_timestamp": rows[0]["timestamp"],
        "finish_timestamp": rows[-1]["timestamp"],
        "sample_rate_hz": 200,
        "columns": CHANNELS,
        "rows": [[float(r[c]) for c in CHANNELS] for r in rows],
    }


def bundle_from_delivery():
    if not os.path.exists(RUNS):
        sys.exit(
            f"no delivery state at {RUNS}\n"
            "run fit/deliver.py first (Stage 6), or pass --bundle"
        )
    return json.load(open(RUNS))["bundle"]


def deploy(bundle_id):
    # `connectors` is required by the run endpoint even here, where the agent is
    # fed over /query rather than from files -- so the source list is empty.
    agent = request("POST", f"{agents()}/bundles/{bundle_id}/run",
                    body={"mode": "serving", "connectors": {"source": []}})
    return agent["id"]


def wait_running(agent_id, timeout_s=60):
    """A serving run is created `running` -- nothing is dispatched. Polled anyway,
    so this stage still reads correctly once a runtime is placed behind it."""
    deadline = time.time() + timeout_s
    seen = None
    while True:
        status = request("GET", f"{agents()}/instances/{agent_id}")["status"]
        if status != seen:
            print(f"  {agent_id}: {status}")
            seen = status
        if status == "running":
            return
        if time.time() > deadline:
            sys.exit(f"{agent_id} did not reach running within {timeout_s}s (last: {status})")
        time.sleep(1)


def over_query(agent_id, windows):
    print(f"\n/query -- one request in, one answer out, {len(windows)} time(s)")
    for i, rows in enumerate(windows):
        started = time.time()
        answer = request("POST", f"{agents()}/instances/{agent_id}/query", body=payload(i, rows))
        rtt = (time.time() - started) * 1000
        echoed = answer.get("response", {}).get("window_index")
        print(
            f"  window {i}: status={answer['status']}"
            f"  server={answer['query_response_time_ms']:.1f} ms"
            f"  round trip={rtt:.0f} ms"
            f"  echoed window_index={echoed}"
        )


def over_connect(agent_id, windows):
    print(f"\n/connect -- one socket, {len(windows)} window(s) pushed, answers as they come")
    url = f"{api_base().replace('https://', 'wss://').replace('http://', 'ws://')}" \
          f"/agents/instances/{agent_id}/connect"
    sock = create_connection(url, header=[f"Authorization: Bearer {os.environ['ATAI_API_KEY']}"], timeout=60)
    try:
        opened = json.loads(sock.recv())
        print(f"  <- {opened}")
        if opened.get("status") != "connected":
            sys.exit(f"expected a connected frame, got {opened}")
        for i, rows in enumerate(windows):
            sock.send(json.dumps(payload(i, rows)))
            raw = sock.recv()
            if not raw:
                sys.exit("the session closed before answering")
            frame = json.loads(raw)
            if frame["status"] != "completed":
                print(f"  <- {frame}")
                continue
            print(
                f"  window {i}: status={frame['status']}"
                f"  server={frame['query_response_time_ms']:.1f} ms"
                f"  echoed window_index={frame['response'].get('window_index')}"
            )
    finally:
        sock.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", help="bundle to serve (default: the one Stage 6 delivered)")
    ap.add_argument("--windows", type=int, default=3, help="windows to send over each transport")
    ap.add_argument("--from", dest="source", metavar="CSV",
                    help="role CSV to read windows from (default: the first delivery file)")
    ap.add_argument("--rows", type=int, default=WINDOW_ROWS,
                    help=f"rows per window (default {WINDOW_ROWS}: 2.56 s at 200 Hz)")
    ap.add_argument("--keep", action="store_true", help="leave the agent running instead of cancelling it")
    args = ap.parse_args()

    load_dotenv()
    if not os.environ.get("ATAI_API_KEY"):
        sys.exit("ATAI_API_KEY is not set (see .env.example)")

    source = args.source or default_role_file()
    if not source:
        sys.exit(
            "no role files found under data/roles/delivery\n"
            "build them first (Stages 0-2, or the Shortcut in the README), or pass --from"
        )
    windows = read_windows(source, args.windows, args.rows)
    print(f"{len(windows)} window(s) of {args.rows} rows from {os.path.relpath(source, ROOT)}")

    bundle_id = args.bundle or bundle_from_delivery()
    print(f"bundle {bundle_id} -- the same one Stage 6 delivered")

    agent_id = deploy(bundle_id)
    print(f"serving agent {agent_id}")
    wait_running(agent_id)

    try:
        over_query(agent_id, windows)
        over_connect(agent_id, windows)
    finally:
        if args.keep:
            print(f"\nleft running: {agent_id} (cancel with POST {agents()}/instances/{agent_id}/cancel)")
        else:
            request("POST", f"{agents()}/instances/{agent_id}/cancel", body={})
            print(f"\ncancelled {agent_id}")


if __name__ == "__main__":
    main()
