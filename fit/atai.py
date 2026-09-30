"""Archetype AI platform helpers, stdlib only (adapted from the Volve example's run_osm_example.py).

Reads ATAI_API_KEY and ATAI_API_ENDPOINT from .env. Platform routes are
<endpoint>/agents/...; file uploads go to <endpoint>/v0.5/files.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMINAL = {"completed", "failed", "canceled", "cancelled"}   # the platform spells it "cancelled"


def load_dotenv(path=os.path.join(ROOT, ".env")):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def api_base():
    endpoint = os.environ.get("ATAI_API_ENDPOINT", "").rstrip("/")
    if not endpoint:
        sys.exit("ATAI_API_ENDPOINT is not set (see .env.example)")
    return endpoint


def agents():
    return f"{api_base()}/agents"


def request(method, url, body=None):
    """JSON request. GETs retry through brief outages; POSTs never retry (a repeat could duplicate)."""
    data = json.dumps(body).encode() if body is not None else None
    delay, waited = 5, 0
    while True:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if method != "GET" or e.code not in (502, 503, 504) or waited >= 600:
                raise RuntimeError(f"{method} {url} failed ({e.code}): {detail}") from None
            reason = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            if method != "GET" or waited >= 600:
                raise
            reason = str(e.reason)
        print(f"  {method} {url} unavailable ({reason}); retrying in {delay}s", file=sys.stderr)
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 60)


def upload_file(path, rename=None):
    """POST a file to /v0.5/files as multipart/form-data; returns the platform's JSON (with file_id)."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{rename or os.path.basename(path)}"\r\n'.encode(),
        b"Content-Type: text/csv\r\n\r\n", content, f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(f"{api_base()}/v0.5/files", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    # Retried through brief network or gateway outages (a DNS blip failed a whole search once).
    # A retry after a lost response can leave an unused copy of the file on the platform; harmless.
    delay, waited = 5, 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code not in (502, 503, 504) or waited >= 600:
                raise RuntimeError(f"upload of {path} failed ({e.code}): {detail}") from None
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if waited >= 600:
                raise
            reason = str(getattr(e, "reason", e))
        print(f"  upload of {os.path.basename(path)} unavailable ({reason}); retrying in {delay}s", file=sys.stderr)
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 60)


def list_trials(opt_id):
    trials, cursor = [], ""
    while True:
        page = request("GET", f"{agents()}/optimizations/{opt_id}/trials?limit=1000{cursor}")
        trials += page["data"]
        if not page.get("has_more"):
            return trials
        cursor = f"&after={page['next_cursor']}"


def wait_optimization(opt_id, label="", every_s=30, log=print):
    """Poll until the optimization is terminal; returns (optimization, trials)."""
    last = None
    while True:
        opt = request("GET", f"{agents()}/optimizations/{opt_id}")
        p = opt.get("progress", {})
        state = (opt["status"], p.get("completed"), p.get("failed"))
        if state != last:
            log(f"{label} {opt_id}: {opt['status']} {p}")
            last = state
        if opt["status"] in TERMINAL:
            return opt, list_trials(opt_id)
        time.sleep(every_s)


def trial_f1(trial):
    """({class: F1}, windows scored) from a trial's confusion matrix (rows = true, columns = predicted)."""
    s = (trial.get("metrics_report") or {}).get("targets", {}).get("state", {})
    names, cm = s.get("class_names"), s.get("confusion_matrix")
    if not names or not cm:
        return {}, 0
    f1 = {}
    for i, n in enumerate(names):
        tp = cm[i][i]
        fn = sum(cm[i]) - tp
        fp = sum(row[i] for row in cm) - tp
        f1[n] = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
    return f1, sum(map(sum, cm))


def trial_setting(trial):
    """(window, step, k, metric, weights) of a trial."""
    tv = trial["trial_values"]
    return (tv["values"]["window_size"], tv["values"]["step_size"], tv["fitting"]["k_neighbors"],
            tv["fitting"]["metric"], tv["fitting"]["weights"])
