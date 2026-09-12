from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import threading
import os
import math
import hashlib
import secrets

app = FastAPI(title="Attendance QR System")

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data.json"
ADMINS_FILE = BASE_DIR / "admins.json"
SESSIONS_FILE = BASE_DIR / "sessions.json"
CONFIG_FILE = BASE_DIR / "config.json"
TZ = ZoneInfo("Asia/Tehran")

WRITE_LOCK = threading.Lock()   # guards data.json read-modify-write
ADMIN_LOCK = threading.Lock()   # guards admins.json / sessions.json

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


# ---------- attendance data ----------

def load_data():
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        data.setdefault("records", [])
        data.setdefault("teacherProfiles", {})
        return data
    return {"records": [], "teacherProfiles": {}}


def save_data(data):
    tmp_file = DATA_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_file, DATA_FILE)


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


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------- venue / geofence config ----------

DEFAULT_CONFIG = {"venueLat": 35.6892, "venueLng": 51.3890, "radiusMeters": 200}


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- admin accounts + sessions ----------

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def load_admins():
    if ADMINS_FILE.exists():
        return json.loads(ADMINS_FILE.read_text(encoding="utf-8"))
    salt = secrets.token_hex(8)
    admins = [{"username": "admin", "salt": salt, "hash": hash_password("admin1234", salt)}]
    save_admins(admins)
    return admins


def save_admins(admins):
    ADMINS_FILE.write_text(json.dumps(admins, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sessions() -> set:
    if SESSIONS_FILE.exists():
        try:
            return set(json.loads(SESSIONS_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_sessions(sessions: set):
    SESSIONS_FILE.write_text(json.dumps(list(sessions)), encoding="utf-8")


SESSIONS = load_sessions()


def require_admin(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="ورود مدیر لازم است")
    token = authorization.split(" ", 1)[1]
    if token not in SESSIONS:
        raise HTTPException(status_code=401, detail="نشست منقضی شده است، دوباره وارد شوید")
    return token


# ---------- models ----------

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
    lat: float
    lng: float
    deviceId: str


class LookupBody(BaseModel):
    fullName: str


class LoginBody(BaseModel):
    username: str
    password: str


class NewAdminBody(BaseModel):
    username: str
    password: str


class VenueBody(BaseModel):
    venueLat: float
    venueLng: float
    radiusMeters: float


# ---------- public endpoints ----------

@app.get("/api/venue")
def get_venue():
    cfg = load_config()
    return {"venueLat": cfg["venueLat"], "venueLng": cfg["venueLng"], "radiusMeters": cfg["radiusMeters"]}


@app.post("/api/lookup")
def lookup(body: LookupBody):
    with WRITE_LOCK:
        data = load_data()
    profile = data["teacherProfiles"].get(body.fullName.strip())
    return {"profile": profile}


@app.post("/api/checkin")
def checkin(body: CheckinBody, request: Request):
    cfg = load_config()
    dist = haversine_m(body.lat, body.lng, cfg["venueLat"], cfg["venueLng"])
    if dist > cfg["radiusMeters"]:
        return {"status": "out_of_range", "distance": round(dist), "allowed": cfg["radiusMeters"]}

    full_name = f"{body.firstName.strip()} {body.lastName.strip()}".strip()
    client_ip = get_client_ip(request)

    with WRITE_LOCK:
        data = load_data()

        # Anti-proxy-punching: this exact phone (deviceId) may only check in
        # twice per session date — matching the two real periods per day.
        device_uses_today = sum(
            1 for r in data["records"]
            if r.get("deviceId") == body.deviceId and r["sessionDate"] == body.sessionDate
        )
        if device_uses_today >= 2:
            return {"status": "device_limit"}

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
            "distanceMeters": round(dist),
            "deviceId": body.deviceId,
            "ipAddress": client_ip,
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


@app.post("/api/login")
def login(body: LoginBody):
    with ADMIN_LOCK:
        admins = load_admins()
        for a in admins:
            if a["username"] == body.username and hash_password(body.password, a["salt"]) == a["hash"]:
                token = secrets.token_hex(24)
                SESSIONS.add(token)
                save_sessions(SESSIONS)
                return {"status": "ok", "token": token}
    return {"status": "error", "message": "نام کاربری یا رمز عبور اشتباه است"}


@app.post("/api/logout")
def logout(authorization: str = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        with ADMIN_LOCK:
            SESSIONS.discard(token)
            save_sessions(SESSIONS)
    return {"status": "ok"}


# ---------- admin-only endpoints ----------

@app.get("/api/data")
def get_data(token: str = Depends(require_admin)):
    return load_data()


@app.get("/api/admin/list-admins")
def list_admins(token: str = Depends(require_admin)):
    admins = load_admins()
    return {"admins": [a["username"] for a in admins]}


@app.post("/api/admin/create-admin")
def create_admin(body: NewAdminBody, token: str = Depends(require_admin)):
    with ADMIN_LOCK:
        admins = load_admins()
        if any(a["username"] == body.username for a in admins):
            return {"status": "error", "message": "این نام کاربری قبلاً وجود دارد"}
        salt = secrets.token_hex(8)
        admins.append({"username": body.username, "salt": salt, "hash": hash_password(body.password, salt)})
        save_admins(admins)
    return {"status": "ok"}


@app.post("/api/admin/venue")
def set_venue(body: VenueBody, token: str = Depends(require_admin)):
    save_config({"venueLat": body.venueLat, "venueLng": body.venueLng, "radiusMeters": body.radiusMeters})
    return {"status": "ok"}


# Must be mounted last: everything not matched by /api routes above is served
# from the static folder (this is what serves index.html at "/").
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
