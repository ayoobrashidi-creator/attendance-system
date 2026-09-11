from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import threading
import os

app = FastAPI(title="Attendance QR System")

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data.json"
TZ = ZoneInfo("Asia/Tehran")

# Guards every read-modify-write cycle on data.json so that two check-ins
# arriving at (almost) the same instant can never overwrite each other.
WRITE_LOCK = threading.Lock()

# Jalali (Shahrivar 1405) session dates mapped to their real Gregorian date,
# used only to decide whether "today" matches a session date for on-time/late scoring.
SESSION_DATES = {
    "1405/06/21": "2026-09-12",
    "1405/06/22": "2026-09-13",
    "1405/06/23": "2026-09-14",
    "1405/06/24": "2026-09-15",
    "1405/06/25": "2026-09-16",
    "1405/06/26": "2026-09-17",
    "1405/06/27": "2026-09-18",
    "1405/06/28": "2026-09-19",
    "1405/06/29": "2026-09-20",
    "1405/06/30": "2026-09-21",
}
PERIOD_START = {"p1": (8, 0), "p2": (10, 30)}
GRACE_MINUTES = 10


def load_data():
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        data.setdefault("records", [])
        data.setdefault("teacherProfiles", {})
        return data
    return {"records": [], "teacherProfiles": {}}


def save_data(data):
    tmp_file = DATA_FILE.with_suffix(".tmp")
    tmp_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_file, DATA_FILE)  # atomic on the same filesystem


def compute_status(session_date: str, period: str, now: datetime):
    greg = SESSION_DATES.get(session_date)
    if not greg or greg != now.date().isoformat():
        return "ثبت‌شده", 0
    start_h, start_m = PERIOD_START.get(period, (8, 0))
    scheduled = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    delay = (now - scheduled).total_seconds() / 60
    if delay > GRACE_MINUTES:
        return "تأخیر", round(delay)
    return "حاضر", 0


class CheckinBody(BaseModel):
    firstName: str
    lastName: str
    personnelCode: str
    nationalCode: str
    gender: str
    birthDate: str
    maritalStatus: str
    orgPosition: str
    grade: str
    sessionDate: str
    period: str
    course: str
    instructor: str
    degree: str
    teachingExperience: str
    phone: str
    workAddress: str
    homeAddress: str


@app.get("/api/data")
def get_data():
    return load_data()


@app.post("/api/checkin")
def checkin(body: CheckinBody):
    full_name = f"{body.firstName.strip()} {body.lastName.strip()}".strip()

    with WRITE_LOCK:
        data = load_data()

        for r in data["records"]:
            if (
                r["fullName"] == full_name
                and r["sessionDate"] == body.sessionDate
                and r["period"] == body.period
            ):
                return {"status": "duplicate"}

        now = datetime.now(TZ)
        status, delay_minutes = compute_status(body.sessionDate, body.period, now)

        record = {
            "row": len(data["records"]) + 1,
            "fullName": full_name,
            "firstName": body.firstName.strip(),
            "lastName": body.lastName.strip(),
            "personnelCode": body.personnelCode,
            "nationalCode": body.nationalCode,
            "gender": body.gender,
            "birthDate": body.birthDate,
            "maritalStatus": body.maritalStatus,
            "orgPosition": body.orgPosition,
            "grade": body.grade,
            "sessionDate": body.sessionDate,
            "period": body.period,
            "course": body.course,
            "instructor": body.instructor,
            "degree": body.degree,
            "teachingExperience": body.teachingExperience,
            "phone": body.phone,
            "workAddress": body.workAddress,
            "homeAddress": body.homeAddress,
            "status": status,
            "delayMinutes": delay_minutes,
            "submittedAt": now.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        data["records"].append(record)

        data["teacherProfiles"][full_name] = {
            "firstName": body.firstName.strip(),
            "lastName": body.lastName.strip(),
            "personnelCode": body.personnelCode,
            "nationalCode": body.nationalCode,
            "gender": body.gender,
            "birthDate": body.birthDate,
            "maritalStatus": body.maritalStatus,
            "orgPosition": body.orgPosition,
            "grade": body.grade,
            "degree": body.degree,
            "teachingExperience": body.teachingExperience,
            "phone": body.phone,
            "workAddress": body.workAddress,
            "homeAddress": body.homeAddress,
        }

        save_data(data)
        return {"status": "ok", "record": record}


# Must be mounted last: everything not matched by /api routes above is served
# from the static folder (this is what serves index.html at "/").
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
