"""Archetype AI platform helpers, stdlib only (adapted from the Volve example's run_osm_example.py).

Reads ATAI_API_KEY and ATAI_API_ENDPOINT from .env. Every platform surface
lives under one versionless /api prefix: <endpoint>/api/agents/... for the
Agents API, <endpoint>/api/files for uploads.
"""
import json
import os
import re
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
            if not os.environ.get(key.strip()):     # unset, or exported but empty
                os.environ[key.strip()] = value.strip()


def api_base():
    """The deployment root with /api appended — every surface lives under it.

    Accepts a bare root, a /vX.Y suffix or /api itself, so one .env works
    whichever form you have.
    """
    endpoint = os.environ.get("ATAI_API_ENDPOINT", "").rstrip("/")
    if not endpoint:
        sys.exit("ATAI_API_ENDPOINT is not set (see .env.example)")
    endpoint = re.sub(r"/api$", "", endpoint)
    endpoint = re.sub(r"/v[0-9]+(\.[0-9]+)*$", "", endpoint)
    return f"{endpoint}/api"


def deployment(endpoint):
    """What deployment an endpoint addresses, whatever hostname it arrives under.

    api.u1.archetypeai.app and platform.u1.archetypeai.app are one
    deployment under two names: an agent created through either is visible
    through the other. So the generation (api, platform) left of the first dot
    is dropped; the rest still separates u1 from u2.
    """
    host = re.sub(r"^[a-z]+://", "", endpoint.strip()).split("/")[0].split(":")[0]
    return re.sub(r"^(api|platform)\.", "", host).lower()


def same_deployment(state):
    """True if a state file's recorded endpoint addresses this deployment; a file with none is from an earlier run."""
    return bool(state.get("endpoint")) and deployment(state["endpoint"]) == deployment(api_base())


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


PROJECT = "LARCO OSM quickstart"
BASE_KEY = "larco-qs"            # the copy of `osm`; trials it promotes default to <BASE_KEY>-<trial id>


def lifecycle_url(kind, obj_id=""):
    """`packages`, `deployments` or `sessions`, or one of them by id."""
    return f"{agents()}/{kind}" + (f"/{obj_id}" if obj_id else "")


def base_blueprint(out, source="osm", log=print):
    """The quickstart's project and its copy of `source`, which the optimizations search.

    Everything after it lands in the project: an optimization in its blueprint's project, and
    so do the blueprints it promotes and their packages, deployments and sessions. A deployment
    needs one, and the canonical `osm` is outside any, while POST /optimizations takes no
    project_id. Copying needs only the right to modify the project, not the admin role. An
    existing project of that name, and an existing copy under BASE_KEY in it, are reused.

    Writes <out>/project_state.json; returns {project, id, key}."""
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "project_state.json")
    state = json.load(open(path)) if os.path.exists(path) else {}
    if state and not same_deployment(state):
        sys.exit(f"{path} is from {state.get('endpoint')}, a different deployment from {api_base()}: move it aside")
    state["endpoint"] = api_base()
    if "project" not in state:
        found = [p for p in request("GET", f"{api_base()}/projects?limit=1000")["data"] if p["name"] == PROJECT]
        state["project"] = found[0]["id"] if found else request(
            "POST", f"{api_base()}/projects", body={"name": PROJECT, "description": "LARCO washing-machine states (OSM quickstart)"})["id"]
        json.dump(state, open(path, "w"), indent=1)
        log(f"project {state['project']} ({PROJECT})")
    if "base" not in state:
        found = request("GET", f"{agents()}/blueprints?project_id={state['project']}&key={BASE_KEY}")["data"]
        if found:
            bp = found[0]
            log(f"reusing {BASE_KEY} in {state['project']} ({bp['id']})")
        else:
            bp = request("POST", f"{agents()}/blueprints/{source}/copy", body={"project_id": state["project"], "blueprint_key": BASE_KEY})
            log(f"copied {source} into {state['project']} as {BASE_KEY} ({bp['id']})")
        state["base"] = {"id": bp["id"], "key": bp["blueprint_key"]}
        json.dump(state, open(path, "w"), indent=1)
    return {"project": state["project"], **state["base"]}


def _wait_for(kind, obj_id, want, waiting, timeout_s=600, every_s=3):
    t0 = time.time()
    while True:
        obj = request("GET", lifecycle_url(kind, obj_id))
        if obj["status"] == want:
            return obj
        if obj["status"] != waiting or time.time() - t0 > timeout_s:
            sys.exit(f"{obj_id} is {obj['status']}, not {want}: {obj.get('error')}")
        time.sleep(every_s)


def create_package(blueprint_id, name):
    """Package one blueprint for the platform and wait until it is ready. A package pins the
    blueprint and the models it resolves to (the Omega encoder, the fitted kNN) and carries no
    values: it runs the blueprint exactly as promoted."""
    pkg = request("POST", lifecycle_url("packages"),
                  body={"blueprint_ids": [blueprint_id], "target": {"kind": "platform"}, "name": name})
    return _wait_for("packages", pkg["id"], "ready", "building", every_s=5)


def deploy(package_id, kind):
    """Deploy a package as `batch` (ready at once) or `serving` (the agent placed on a worker and
    loaded, usually within seconds; it holds the worker until stopped)."""
    dpl = request("POST", lifecycle_url("deployments"), body={"package_id": package_id, "kind": kind})
    return _wait_for("deployments", dpl["id"], "ready", "deploying")


def open_session(deployment_id, file_ids=None):
    """A batch session over `file_ids` (one output per input), or a serving session, queried at
    /sessions/{id}/query once `running`, when none are given."""
    body = {"deployment_id": deployment_id}
    if file_ids:
        body["connectors"] = {"source": [{"type": "file", "id": f, "format": "csv"} for f in file_ids]}
    return request("POST", lifecycle_url("sessions"), body=body)


def wait_running(session_id):
    return _wait_for("sessions", session_id, "running", "pending")


def end(kind, obj_id):
    """Cancel a session still going, or stop a deployment still up, then delete it; a package is
    deleted as it is. An id already gone counts as done."""
    try:
        obj = request("GET", lifecycle_url(kind, obj_id))
        if kind == "sessions" and obj["status"] not in TERMINAL:
            request("POST", lifecycle_url(kind, obj_id) + "/cancel", body={})
        if kind == "deployments" and obj["status"] not in ("stopped", "failed"):
            request("POST", lifecycle_url(kind, obj_id) + "/stop", body={})
        request("DELETE", lifecycle_url(kind, obj_id))
    except RuntimeError as e:
        if "(404)" not in str(e):
            raise


def result_url(ref):
    """A session result's `ref` is a root path, `/api/files/<fil_…>/content`, or an absolute URL:
    resolve it against the deployment root, not against .../api."""
    return ref if ref.startswith("http") else api_base().removesuffix("/api") + ref


def upload_file(path, rename=None):
    """POST a file to /api/files as multipart/form-data; returns the platform's JSON (with file_id)."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{rename or os.path.basename(path)}"\r\n'.encode(),
        b"Content-Type: text/csv\r\n\r\n", content, f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(f"{api_base()}/files", data=body, method="POST")
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
    v = {**tv.get("fitting", {}), **tv["values"]}    # newer blueprints report every setting under values
    return v["window_size"], v["step_size"], v["k_neighbors"], v["metric"], v["weights"]
