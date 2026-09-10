from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from typing import List
from datetime import datetime
from zoneinfo import ZoneInfo
import json

app = FastAPI(title="Attendance QR System")

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data.json"
TZ = ZoneInfo("Asia/Tehran")

# Start time (hour, minute) for each session — used only to detect lateness,
# never to block a check-in.
SESSION_START = {
    "g1-p1": (8, 0),  "g1-p2": (10, 0),
    "g2-p1": (8, 0),  "g2-p2": (10, 0),
    "g3-p1": (8, 0),  "g3-p2": (10, 0),
    "g4-p1": (8, 0),  "g4-p2": (10, 0),
    "g5-p1": (8, 0),  "g5-p2": (10, 0),
    "g6-p1": (13, 0), "g6-p2": (15, 0),
}
GRACE_MINUTES = 10  # up to this many minutes after start still counts as "حاضر"


def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"teachers": {}, "records": []}


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class CheckinBody(BaseModel):
    sessionId: str
    teacherName: str
    course: str
    grade: str


class TeachersBody(BaseModel):
    grade: str
    names: List[str]


@app.get("/api/data")
def get_data():
    return load_data()


@app.post("/api/checkin")
def checkin(body: CheckinBody):
    data = load_data()
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")

    for r in data["records"]:
        if (
            r["sessionId"] == body.sessionId
            and r["teacherName"] == body.teacherName
            and r["time"].startswith(today)
        ):
            return {"status": "duplicate"}

    start_h, start_m = SESSION_START.get(body.sessionId, (now.hour, now.minute))
    session_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    delay_minutes = int((now - session_start).total_seconds() // 60)

    if delay_minutes <= GRACE_MINUTES:
        status, delay_minutes = "حاضر", 0
    else:
        status = "تأخیر"

    record = {
        "sessionId": body.sessionId,
        "teacherName": body.teacherName,
        "course": body.course,
        "time": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": status,
        "delayMinutes": delay_minutes,
    }
    data["records"].append(record)

    names = data["teachers"].setdefault(body.grade, [])
    if body.teacherName not in names:
        names.append(body.teacherName)

    save_data(data)
    return {"status": "ok", "record": record}


@app.post("/api/teachers")
def set_teachers(body: TeachersBody):
    data = load_data()
    data["teachers"][body.grade] = body.names
    save_data(data)
    return {"status": "ok"}


# Must be mounted last: everything not matched by /api routes above is served
# from the static folder (this is what serves index.html at "/").
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
