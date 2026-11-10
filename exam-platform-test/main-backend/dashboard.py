"""Streamlit frontend with a Test button. Talks to the test-attendee service."""
import os, time
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Exam backend load test", layout="wide")
st.title("Exam backend load test")

c1, c2 = st.columns(2)
backend = c1.text_input("Main backend URL (Render)", os.getenv("BACKEND_URL", "")).rstrip("/")
attendee = c2.text_input("Test attendee URL (Render)", os.getenv("ATTENDEE_URL", "")).rstrip("/")

with st.expander("Test settings", expanded=False):
    s1, s2, s3, s4 = st.columns(4)
    stages_txt = s1.text_input("Concurrent users per stage", "10,25,50,75,100")
    stage_seconds = s2.number_input("Seconds per stage", 5, 120, 15)
    p95_limit = s3.number_input("Pass if p95 below (ms)", 50, 5000, 500)
    max_err = s4.number_input("Pass if errors below (%)", 0.0, 100.0, 1.0)

if st.button("Test", type="primary"):
    if not backend or not attendee:
        st.error("Enter both URLs first.")
        st.stop()
    try:
        stages = [int(x) for x in stages_txt.split(",") if x.strip()]
    except ValueError:
        st.error("Stages must be comma-separated numbers, e.g. 10,25,50")
        st.stop()

    with st.spinner("Waking up test attendee service (free Render can take ~1 min)..."):
        try:
            requests.get(f"{attendee}/health", timeout=120).raise_for_status()
        except Exception as e:
            st.error(f"Cannot reach test attendee: {e}")
            st.stop()

    r = requests.post(f"{attendee}/start", timeout=30, json={
        "target_url": backend, "stages": stages, "stage_seconds": int(stage_seconds),
        "p95_limit_ms": float(p95_limit), "max_error_rate": max_err / 100})
    if r.status_code != 200:
        st.error(r.text)
        st.stop()
    run_id = r.json()["run_id"]

    bar, note = st.progress(0.0), st.empty()
    run = {}
    while True:
        run = requests.get(f"{attendee}/runs/{run_id}", timeout=30).json()
        done = len(run.get("stages", []))
        bar.progress(min(done / len(stages), 1.0))
        note.info(f"Status: {run['status']} - {run.get('message', '')}")
        if run["status"] in ("done", "failed"):
            break
        time.sleep(2)
    note.empty()

    if run["status"] == "failed":
        st.error(run.get("message", "Test failed"))
        st.stop()

    o = run["overall"]
    st.subheader("Result")
    m = st.columns(4)
    m[0].metric("Max concurrent users handled", run["max_users_passed"])
    m[1].metric("Total requests sent", f"{o['total_requests']:,}")
    m[2].metric("Successful / Failed", f"{o['successful']:,} / {o['failed']:,}")
    m[3].metric("Error rate", f"{o['error_rate']*100:.2f}%")
    m = st.columns(4)
    m[0].metric("Peak throughput", f"{run['peak_rps_passed']:.0f} req/s")
    m[1].metric("Latency p50", f"{o['p50_ms']:.0f} ms")
    m[2].metric("Latency p95", f"{o['p95_ms']:.0f} ms")
    m[3].metric("Latency p99 / max", f"{o['p99_ms']:.0f} / {o['max_ms']:.0f} ms")

    if run["max_users_passed"] == 0:
        st.warning("Even the first stage failed the thresholds. Try fewer users, e.g. 5,10,25.")
    else:
        st.success(f"Backend held up to {run['max_users_passed']} concurrent users "
                   f"(p95 < {p95_limit} ms, errors < {max_err}%).")

    df = pd.DataFrame(run["stages"])
    df["result"] = df["passed"].map({True: "PASS", False: "FAIL"})
    st.subheader("Per stage")
    st.dataframe(df[["users", "requests", "rps", "p50_ms", "p95_ms", "p99_ms",
                     "error_rate", "result"]].round(2), use_container_width=True, hide_index=True)
    st.line_chart(df.set_index("users")[["p50_ms", "p95_ms", "p99_ms"]])

    st.subheader("By endpoint")
    ep = pd.DataFrame(run["endpoints"]).T.round(1)
    st.dataframe(ep, use_container_width=True)
    if run["errors"]:
        st.subheader("Errors (status code: count, 0 = timeout/connection)")
        st.json(run["errors"])
