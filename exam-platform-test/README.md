# Exam backend load test

exam-platform-test/
  main-backend/   FastAPI exam API (the thing being tested) + dashboard.py (Streamlit, Test button)
  test-attendee/  FastAPI load generator that simulates candidates

## Deploy on Render (3 services, each from its own folder)
| Service | Root Directory | Build command | Start command |
|---|---|---|---|
| main backend | main-backend | pip install -r requirements.txt | uvicorn app:app --host 0.0.0.0 --port $PORT |
| test attendee | test-attendee | pip install -r requirements.txt | uvicorn app:app --host 0.0.0.0 --port $PORT |
| dashboard (Streamlit) | main-backend | pip install -r dashboard-requirements.txt | streamlit run dashboard.py --server.port $PORT --server.address 0.0.0.0 --server.headless true |

Dashboard env vars (optional, prefill the boxes): BACKEND_URL, ATTENDEE_URL.
Or run the dashboard on your laptop: pip install -r main-backend/dashboard-requirements.txt && streamlit run main-backend/dashboard.py

## Use
Open the dashboard, paste the two Render URLs, click Test.
