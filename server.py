import os
import sqlite3
import secrets
import io
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from contextlib import contextmanager

import qrcode
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from passlib.context import CryptContext
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "znxadmin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ZNXHIKHETE")
DB_PATH = os.getenv("DB_PATH", "data.db")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))

ALGO = "HS256"
TOKEN_TTL_HOURS = 72

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="ZNX X GLORY V92")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# DATABASE
# ============================================================
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            suspended INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price INTEGER NOT NULL,
            duration_hours INTEGER NOT NULL,
            features TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            admin_note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            plan_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # default settings
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('upi_id', 'znx@upi')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('upi_name', 'ZNX GLORY')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('demo_counter_min', '180')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('demo_counter_max', '260')")

        # default plans
        cur = c.execute("SELECT COUNT(*) FROM plans")
        if cur.fetchone()[0] == 0:
            now = datetime.now(timezone.utc).isoformat()
            c.execute("""INSERT INTO plans (title, description, price, duration_hours, features, active, created_at)
                         VALUES (?, ?, ?, ?, ?, 1, ?)""",
                      ("STARTER", "Entry plan for new guilds", 99, 24,
                       "24h bot runtime|Basic match cycling|Email support", now))
            c.execute("""INSERT INTO plans (title, description, price, duration_hours, features, active, created_at)
                         VALUES (?, ?, ?, ?, ?, 1, ?)""",
                      ("PRO", "Balanced plan for active guilds", 299, 72,
                       "72h bot runtime|Priority matchmaking|Glory tracking|Priority support", now))
            c.execute("""INSERT INTO plans (title, description, price, duration_hours, features, active, created_at)
                         VALUES (?, ?, ?, ?, ?, 1, ?)""",
                      ("ELITE", "Maximum runtime for top guilds", 699, 168,
                       "168h bot runtime|Max concurrent matches|Live dashboard|24/7 support", now))

init_db()

# ============================================================
# UTILS
# ============================================================
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def make_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "u": username,
        "r": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGO])
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

def get_bearer(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization header")
    return authorization.split(" ", 1)[1]

def current_user(authorization: Optional[str] = Header(None)) -> dict:
    payload = decode_token(get_bearer(authorization))
    with db() as c:
        row = c.execute("SELECT id, username, role, suspended FROM users WHERE id=?",
                        (int(payload["sub"]),)).fetchone()
    if not row:
        raise HTTPException(401, "User not found")
    if row["suspended"]:
        raise HTTPException(403, "Account suspended")
    return dict(row)

def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    return user

def log_activity(actor: str, action: str, detail: str = ""):
    with db() as c:
        c.execute("INSERT INTO activity_logs (actor, action, detail, created_at) VALUES (?,?,?,?)",
                  (actor, action, detail, now_iso()))

# ============================================================
# MODELS
# ============================================================
class RegisterIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=4, max_length=128)

    @validator("username")
    def u_clean(cls, v):
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username: letters, numbers, _ and - only")
        return v

class LoginIn(BaseModel):
    username: str
    password: str

class PlanIn(BaseModel):
    title: str
    description: str
    price: int = Field(..., ge=1)
    duration_hours: int = Field(..., ge=1)
    features: str
    active: int = 1

class OrderIn(BaseModel):
    plan_id: int
    guild_id: str = Field(..., min_length=4, max_length=32)

class ReviewIn(BaseModel):
    order_id: str
    action: str  # approve | reject
    admin_note: str = ""

class SettingIn(BaseModel):
    key: str
    value: str

# ============================================================
# AUTH ROUTES
# ============================================================
@app.post("/api/auth/register")
def register(body: RegisterIn):
    with db() as c:
        exists = c.execute("SELECT id FROM users WHERE username=?", (body.username,)).fetchone()
        if exists:
            raise HTTPException(400, "Username already taken")
        ph = pwd_ctx.hash(body.password)
        cur = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                        (body.username, ph, "user", now_iso()))
        uid = cur.lastrowid
        c.execute("INSERT INTO activity_logs (actor, action, detail, created_at) VALUES (?,?,?,?)",
                  (body.username, "REGISTER", "", now_iso()))
    return {"token": make_token(uid, body.username, "user"),
            "user": {"id": uid, "username": body.username, "role": "user"}}

@app.post("/api/auth/login")
def login(body: LoginIn):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    if not row or not pwd_ctx.verify(body.password, row["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    if row["suspended"]:
        raise HTTPException(403, "Account suspended")
    log_activity(body.username, "LOGIN", "")
    return {"token": make_token(row["id"], row["username"], row["role"]),
            "user": {"id": row["id"], "username": row["username"], "role": row["role"]}}

@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return user

# ============================================================
# PUBLIC: PLANS, SETTINGS
# ============================================================
@app.get("/api/plans")
def list_plans():
    with db() as c:
        rows = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY price ASC").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/settings/public")
def public_settings():
    with db() as c:
        rows = c.execute("SELECT key, value FROM admin_settings WHERE key IN ('demo_counter_min','demo_counter_max')").fetchall()
    return {r["key"]: r["value"] for r in rows}

@app.get("/api/activity/recent")
def recent_activity():
    with db() as c:
        rows = c.execute("SELECT actor, action, detail, created_at FROM activity_logs ORDER BY id DESC LIMIT 20").fetchall()
    return [dict(r) for r in rows]

# ============================================================
# ORDERS
# ============================================================
@app.post("/api/orders")
def create_order(body: OrderIn, user: dict = Depends(current_user)):
    with db() as c:
        plan = c.execute("SELECT * FROM plans WHERE id=? AND active=1", (body.plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "Plan not found or inactive")

        # duplicate guard: pending order on same plan within 10 min
        recent = c.execute("""SELECT order_id FROM orders WHERE user_id=? AND plan_id=?
                              AND status='PENDING' AND created_at > ?""",
                           (user["id"], body.plan_id,
                            (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())).fetchone()
        if recent:
            raise HTTPException(400, "You already have a pending order for this plan")

        oid = "ZNX" + secrets.token_hex(6).upper()
        c.execute("""INSERT INTO orders (order_id, user_id, plan_id, guild_id, amount, status, created_at)
                     VALUES (?,?,?,?,?,?,?)""",
                  (oid, user["id"], body.plan_id, body.guild_id, plan["price"], "PENDING", now_iso()))
        c.execute("INSERT INTO activity_logs (actor, action, detail, created_at) VALUES (?,?,?,?)",
                  (user["username"], "ORDER_CREATED", f"{oid} | plan={plan['title']}", now_iso()))

    return {"order_id": oid, "amount": plan["price"], "plan_title": plan["title"],
            "duration_hours": plan["duration_hours"], "status": "PENDING"}

@app.get("/api/orders/my")
def my_orders(user: dict = Depends(current_user)):
    with db() as c:
        rows = c.execute("""
            SELECT o.*, p.title as plan_title FROM orders o
            JOIN plans p ON p.id = o.plan_id
            WHERE o.user_id=? ORDER BY o.id DESC""", (user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/orders/{order_id}/qr")
def order_qr(order_id: str, user: dict = Depends(current_user)):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not o:
            raise HTTPException(404, "Order not found")
        if o["user_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(403, "Not your order")
        upi_id = c.execute("SELECT value FROM admin_settings WHERE key='upi_id'").fetchone()["value"]
        upi_name = c.execute("SELECT value FROM admin_settings WHERE key='upi_name'").fetchone()["value"]

    upi_uri = f"upi://pay?pa={upi_id}&pn={upi_name}&am={o['amount']}.00&cu=INR&tn={order_id}"
    img = qrcode.make(upi_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    return {"qr_png_base64": b64, "amount": o["amount"], "order_id": order_id,
            "upi_uri": upi_uri, "merchant_name": upi_name}

# ============================================================
# SUBSCRIPTIONS
# ============================================================
@app.get("/api/subscriptions/my")
def my_subs(user: dict = Depends(current_user)):
    with db() as c:
        rows = c.execute("""
            SELECT s.*, p.title as plan_title FROM subscriptions s
            JOIN plans p ON p.id = s.plan_id
            WHERE s.user_id=? ORDER BY s.id DESC""", (user["id"],)).fetchall()
    out = []
    now = datetime.now(timezone.utc)
    for r in rows:
        r = dict(r)
        exp = datetime.fromisoformat(r["expires_at"])
        if r["status"] == "ACTIVE" and now >= exp:
            r["status"] = "EXPIRED"
            with db() as c:
                c.execute("UPDATE subscriptions SET status='EXPIRED' WHERE id=?", (r["id"],))
        rem = max(0, int((exp - now).total_seconds()))
        r["remaining_seconds"] = rem
        out.append(r)
    return out

# ============================================================
# ADMIN ROUTES
# ============================================================
@app.get("/api/admin/overview")
def admin_overview(admin: dict = Depends(admin_user)):
    with db() as c:
        total_users = c.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0]
        active_subs = c.execute("SELECT COUNT(*) FROM subscriptions WHERE status='ACTIVE'").fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM orders WHERE status='PENDING'").fetchone()[0]
        approved = c.execute("SELECT COUNT(*) FROM orders WHERE status='APPROVED'").fetchone()[0]
        rejected = c.execute("SELECT COUNT(*) FROM orders WHERE status='REJECTED'").fetchone()[0]
        expired = c.execute("SELECT COUNT(*) FROM subscriptions WHERE status='EXPIRED'").fetchone()[0]
    return {"total_users": total_users, "active_subscriptions": active_subs,
            "pending_payments": pending, "approved_payments": approved,
            "rejected_payments": rejected, "expired_subscriptions": expired}

@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(admin_user)):
    with db() as c:
        rows = c.execute("SELECT id, username, role, suspended, created_at FROM users ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/users/{uid}/suspend")
def admin_suspend(uid: int, admin: dict = Depends(admin_user)):
    with db() as c:
        c.execute("UPDATE users SET suspended=1 WHERE id=?", (uid,))
    log_activity(admin["username"], "USER_SUSPEND", f"uid={uid}")
    return {"ok": True}

@app.post("/api/admin/users/{uid}/reactivate")
def admin_reactivate(uid: int, admin: dict = Depends(admin_user)):
    with db() as c:
        c.execute("UPDATE users SET suspended=0 WHERE id=?", (uid,))
    log_activity(admin["username"], "USER_REACTIVATE", f"uid={uid}")
    return {"ok": True}

@app.get("/api/admin/orders")
def admin_orders(status: Optional[str] = None, admin: dict = Depends(admin_user)):
    q = """SELECT o.*, u.username, p.title as plan_title FROM orders o
           JOIN users u ON u.id = o.user_id
           JOIN plans p ON p.id = o.plan_id"""
    args = []
    if status:
        q += " WHERE o.status=?"
        args.append(status)
    q += " ORDER BY o.id DESC"
    with db() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/orders/review")
def admin_review(body: ReviewIn, admin: dict = Depends(admin_user)):
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")

    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_id=?", (body.order_id,)).fetchone()
        if not o:
            raise HTTPException(404, "Order not found")
        if o["status"] != "PENDING":
            raise HTTPException(400, f"Order already {o['status']}")

        if body.action == "reject":
            c.execute("UPDATE orders SET status='REJECTED', admin_note=?, reviewed_at=? WHERE order_id=?",
                      (body.admin_note, now_iso(), body.order_id))
            log_activity(admin["username"], "ORDER_REJECT", body.order_id)
            return {"ok": True, "status": "REJECTED"}

        # approve
        plan = c.execute("SELECT * FROM plans WHERE id=?", (o["plan_id"],)).fetchone()
        started = datetime.now(timezone.utc)
        expires = started + timedelta(hours=plan["duration_hours"])

        c.execute("UPDATE orders SET status='APPROVED', admin_note=?, reviewed_at=? WHERE order_id=?",
                  (body.admin_note, now_iso(), body.order_id))
        c.execute("""INSERT INTO subscriptions (user_id, order_id, plan_id, guild_id, started_at, expires_at, status)
                     VALUES (?,?,?,?,?,?,?)""",
                  (o["user_id"], o["order_id"], o["plan_id"], o["guild_id"],
                   started.isoformat(), expires.isoformat(), "ACTIVE"))
        log_activity(admin["username"], "ORDER_APPROVE", body.order_id)

    return {"ok": True, "status": "APPROVED", "expires_at": expires.isoformat()}

# ---- Plans management ----
@app.get("/api/admin/plans")
def admin_plans(admin: dict = Depends(admin_user)):
    with db() as c:
        rows = c.execute("SELECT * FROM plans ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/plans")
def admin_plan_create(body: PlanIn, admin: dict = Depends(admin_user)):
    with db() as c:
        cur = c.execute("""INSERT INTO plans (title, description, price, duration_hours, features, active, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (body.title, body.description, body.price, body.duration_hours,
                         body.features, body.active, now_iso()))
    log_activity(admin["username"], "PLAN_CREATE", body.title)
    return {"id": cur.lastrowid}

@app.put("/api/admin/plans/{pid}")
def admin_plan_update(pid: int, body: PlanIn, admin: dict = Depends(admin_user)):
    with db() as c:
        c.execute("""UPDATE plans SET title=?, description=?, price=?, duration_hours=?, features=?, active=?
                     WHERE id=?""",
                  (body.title, body.description, body.price, body.duration_hours,
                   body.features, body.active, pid))
    log_activity(admin["username"], "PLAN_UPDATE", f"id={pid}")
    return {"ok": True}

@app.delete("/api/admin/plans/{pid}")
def admin_plan_delete(pid: int, admin: dict = Depends(admin_user)):
    with db() as c:
        c.execute("DELETE FROM plans WHERE id=?", (pid,))
    log_activity(admin["username"], "PLAN_DELETE", f"id={pid}")
    return {"ok": True}

# ---- Settings ----
@app.get("/api/admin/settings")
def admin_settings_get(admin: dict = Depends(admin_user)):
    with db() as c:
        rows = c.execute("SELECT key, value FROM admin_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}

@app.post("/api/admin/settings")
def admin_settings_set(body: SettingIn, admin: dict = Depends(admin_user)):
    allowed = {"upi_id", "upi_name", "demo_counter_min", "demo_counter_max"}
    if body.key not in allowed:
        raise HTTPException(400, "Unsupported key")
    with db() as c:
        c.execute("INSERT OR REPLACE INTO admin_settings (key, value) VALUES (?,?)",
                  (body.key, body.value))
    log_activity(admin["username"], "SETTING_UPDATE", f"{body.key}")
    return {"ok": True}

@app.get("/api/admin/logs")
def admin_logs(admin: dict = Depends(admin_user)):
    with db() as c:
        rows = c.execute("SELECT actor, action, detail, created_at FROM activity_logs ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]

# ============================================================
# FRONTEND
# ============================================================
@app.get("/", response_class=HTMLResponse)
def index():
    with open("frontend.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)