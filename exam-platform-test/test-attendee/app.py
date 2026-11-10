"""Test attendee service: simulates many candidates hitting the main backend."""
import asyncio, random, time, uuid
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Test attendee (load generator)")
RUNS: dict = {}


class StartReq(BaseModel):
    target_url: str
    stages: list[int] = [10, 25, 50, 75, 100]
    stage_seconds: int = 15
    think_time: float = 0.2          # pause between a candidate's actions
    p95_limit_ms: float = 500
    max_error_rate: float = 0.01
    stop_on_fail: bool = True


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def summarize(recs):
    lat = [r[1] for r in recs]
    fails = [r for r in recs if not r[2]]
    n = len(recs)
    return {"p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95), "p99_ms": pct(lat, 99),
            "max_ms": max(lat) if lat else 0.0, "avg_ms": sum(lat) / n if n else 0.0,
            "requests": n, "failed": len(fails), "error_rate": len(fails) / n if n else 0.0}


async def call(client, recs, name, method, url, **kw):
    t = time.perf_counter()
    code, ok, resp = 0, False, None
    try:
        resp = await client.request(method, url, **kw)
        code, ok = resp.status_code, resp.status_code < 400
    except Exception:
        pass
    recs.append((name, (time.perf_counter() - t) * 1000, ok, code))
    return resp if ok else None


async def virtual_user(client, base, deadline, think, recs):
    r = await call(client, recs, "join", "POST", f"{base}/v1/exams/exam-1/join",
                   json={"candidate_id": uuid.uuid4().hex})
    if not r:
        return
    j = r.json()
    sid, h = j["session_id"], {"Authorization": f"Bearer {j['token']}"}
    v = 0
    while time.time() < deadline:
        await call(client, recs, "get_exam", "GET", f"{base}/v1/sessions/{sid}/exam", headers=h)
        v += 1
        await call(client, recs, "autosave", "PUT",
                   f"{base}/v1/sessions/{sid}/answers/q{random.randint(1, 20)}",
                   headers=h, json={"answer": random.choice("ABCD"), "version": v})
        if random.random() < 0.3:
            await call(client, recs, "event", "POST", f"{base}/v1/sessions/{sid}/events",
                       headers=h, json={"type": "look_away", "confidence": 0.7})
        await asyncio.sleep(think)
    await call(client, recs, "submit", "POST", f"{base}/v1/sessions/{sid}/submit", headers=h)


async def warmup(client, base, run):
    for i in range(30):  # Render free tier may need ~50s to wake
        run["message"] = f"Waking main backend (try {i + 1}/30)"
        try:
            if (await client.get(f"{base}/health", timeout=10)).status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False


async def run_test(run_id: str, req: StartReq):
    run, base = RUNS[run_id], req.target_url.rstrip("/")
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=500)
    all_recs = []
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=10, limits=limits) as client:
            run["status"] = "warming_up"
            if not await warmup(client, base, run):
                run.update(status="failed", message="Main backend did not respond to /health")
                return
            run["status"] = "running"
            for n in req.stages:
                run["message"] = f"Stage: {n} concurrent users for {req.stage_seconds}s"
                recs, ts = [], time.time()
                deadline = ts + req.stage_seconds
                await asyncio.gather(*[virtual_user(client, base, deadline, req.think_time, recs)
                                       for _ in range(n)])
                elapsed = time.time() - ts
                st = summarize(recs)
                st.update(users=n, rps=len(recs) / elapsed,
                          passed=st["p95_ms"] <= req.p95_limit_ms and st["error_rate"] <= req.max_error_rate)
                run["stages"].append(st)
                all_recs += recs
                if not st["passed"] and req.stop_on_fail:
                    break
    except Exception as e:
        run.update(status="failed", message=f"Load generator error: {e}")
        return

    passed = []
    for s in run["stages"]:  # capacity = last stage passed before the first failure
        if not s["passed"]:
            break
        passed.append(s)
    run["max_users_passed"] = passed[-1]["users"] if passed else 0
    run["peak_rps_passed"] = max((s["rps"] for s in passed), default=0.0)
    run["overall"] = summarize(all_recs)
    run["overall"].update(total_requests=len(all_recs), successful=len(all_recs) - run["overall"]["failed"],
                          duration_s=time.time() - t0)
    eps = {}
    for name in {r[0] for r in all_recs}:
        e = summarize([r for r in all_recs if r[0] == name])
        eps[name] = {k: e[k] for k in ("requests", "failed", "p50_ms", "p95_ms", "max_ms")}
    run["endpoints"] = eps
    errs = {}
    for r in all_recs:
        if not r[2]:
            errs[str(r[3])] = errs.get(str(r[3]), 0) + 1
    run["errors"] = errs
    run.update(status="done", message="Finished")


@app.get("/")
@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/start")
async def start(req: StartReq):
    if any(r["status"] in ("queued", "warming_up", "running") for r in RUNS.values()):
        raise HTTPException(409, "A test is already running")
    if not req.stages or max(req.stages) > 1000 or req.stage_seconds > 120:
        raise HTTPException(400, "stages must be <=1000 users and stage_seconds <=120")
    run_id = uuid.uuid4().hex[:8]
    RUNS[run_id] = {"status": "queued", "message": "Queued", "stages": []}
    asyncio.create_task(run_test(run_id, req))
    return {"run_id": run_id}


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "unknown run")
    return RUNS[run_id]
