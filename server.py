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
from pydantic import BaseModel, Field
import bcrypt
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



app = FastAPI(title="ZNX X GLORY V92")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    with db() as c:
        c.executescript('''
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
            reviewed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            plan_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL
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
        ''')
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('upi_id', 'znx@upi')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('upi_name', 'ZNX GLORY')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('demo_counter_min', '180')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('demo_counter_max', '260')")
        
        cur = c.execute("SELECT COUNT(*) FROM plans")
        if cur.fetchone()[0] == 0:
            n = now_iso()
            c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES (?,?,?,?,?,1,?)", ("STARTER", "Entry plan", 99, 24, "24h bot runtime|Basic match cycling", n))
            c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES (?,?,?,?,?,1,?)", ("PRO", "Balanced plan", 299, 72, "72h bot runtime|Priority matchmaking", n))
            c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES (?,?,?,?,?,1,?)", ("ELITE", "Maximum runtime", 699, 168, "168h bot runtime|Max matches", n))

def ensure_admin():
    with db() as c:
        admin = c.execute("SELECT * FROM users WHERE role='admin'").fetchone()
        if not admin:
            ph = bcrypt.hashpw(ADMIN_PASSWORD[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                      (ADMIN_USERNAME, ph, "admin", now_iso()))

init_db()
ensure_admin()

def make_token(user_id: int, username: str, role: str) -> str:
    payload = {"sub": str(user_id), "u": username, "r": role, "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGO])
    except JWTError:
        raise HTTPException(401, "Invalid token")

def get_bearer(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing header")
    return authorization.split(" ", 1)[1]

def current_user(authorization: Optional[str] = Header(None)) -> dict:
    payload = decode_token(get_bearer(authorization))
    with db() as c:
        row = c.execute("SELECT id, username, role, suspended FROM users WHERE id=?", (int(payload["sub"]),)).fetchone()
    if not row or row["suspended"]:
        raise HTTPException(401, "User not found or suspended")
    return dict(row)

def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    return user

def log_activity(actor: str, action: str, detail: str = ""):
    with db() as c:
        c.execute("INSERT INTO activity_logs (actor, action, detail, created_at) VALUES (?,?,?,?)", (actor, action, detail, now_iso()))

class RegisterIn(BaseModel):
    username: str
    password: str
class LoginIn(BaseModel):
    username: str
    password: str
class PlanIn(BaseModel):
    title: str
    description: str
    price: int
    duration_hours: int
    features: str
    active: int = 1
class OrderIn(BaseModel):
    plan_id: int
    guild_id: str
class ReviewIn(BaseModel):
    order_id: str
    action: str
    admin_note: str = ""
class SettingIn(BaseModel):
    key: str
    value: str

@app.post("/api/auth/register")
def register(body: RegisterIn):
    with db() as c:
        exists = c.execute("SELECT id FROM users WHERE username=?", (body.username,)).fetchone()
        if exists: raise HTTPException(400, "Username taken")
        ph = bcrypt.hashpw(body.password[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)", (body.username, ph, "user", now_iso()))
        uid = cur.lastrowid
    return {"token": make_token(uid, body.username, "user"), "user": {"id": uid, "username": body.username, "role": "user"}}

@app.post("/api/auth/login")
def login(body: LoginIn):
    # HARDCODED ADMIN BYPASS (Fixes Render DB issues)
    if body.username == ADMIN_USERNAME and body.password == ADMIN_PASSWORD:
        with db() as c:
            # Ensure admin exists in DB for other features
            admin = c.execute("SELECT * FROM users WHERE username=?", (ADMIN_USERNAME,)).fetchone()
            if not admin:
                ph = bcrypt.hashpw(ADMIN_PASSWORD[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                cur = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)", (ADMIN_USERNAME, ph, "admin", now_iso()))
                uid = cur.lastrowid
            else:
                uid = admin["id"]
        return {"token": make_token(uid, ADMIN_USERNAME, "admin"), "user": {"id": uid, "username": ADMIN_USERNAME, "role": "admin"}}
        
    # Normal User Login
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    if not row or not bcrypt.checkpw(body.password[:72].encode("utf-8"), row["password_hash"].encode("utf-8")): 
        raise HTTPException(401, "Invalid credentials")
    if row["suspended"]: 
        raise HTTPException(403, "Account suspended")
    return {"token": make_token(row["id"], row["username"], row["role"]), "user": {"id": row["id"], "username": row["username"], "role": row["role"]}}

@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)): return user

@app.get("/api/plans")
def list_plans():
    with db() as c: rows = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY price ASC").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/settings/public")
def public_settings():
    with db() as c: rows = c.execute("SELECT key, value FROM admin_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}

@app.post("/api/orders")
def create_order(body: OrderIn, user: dict = Depends(current_user)):
    with db() as c:
        plan = c.execute("SELECT * FROM plans WHERE id=? AND active=1", (body.plan_id,)).fetchone()
        if not plan: raise HTTPException(404, "Plan not found")
        oid = "ZNX" + secrets.token_hex(6).upper()
        c.execute("INSERT INTO orders (order_id, user_id, plan_id, guild_id, amount, status, created_at) VALUES (?,?,?,?,?,'PENDING',?)", (oid, user["id"], body.plan_id, body.guild_id, plan["price"], now_iso()))
    return {"order_id": oid, "amount": plan["price"]}

@app.get("/api/orders/my")
def my_orders(user: dict = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT o.*, p.title as plan_title FROM orders o JOIN plans p ON p.id = o.plan_id WHERE o.user_id=? ORDER BY o.id DESC", (user["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/orders/{order_id}/qr")
def order_qr(order_id: str, user: dict = Depends(current_user)):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not o: raise HTTPException(404, "Not found")
        upi_id = c.execute("SELECT value FROM admin_settings WHERE key='upi_id'").fetchone()["value"]
        upi_name = c.execute("SELECT value FROM admin_settings WHERE key='upi_name'").fetchone()["value"]
    uri = f"upi://pay?pa={upi_id}&pn={upi_name}&am={o['amount']}.00&cu=INR&tn={order_id}"
    img = qrcode.make(uri)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return {"qr_png_base64": base64.b64encode(buf.getvalue()).decode(), "amount": o["amount"], "order_id": order_id}

@app.get("/api/subscriptions/my")
def my_subs(user: dict = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT s.*, p.title as plan_title FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.user_id=? ORDER BY s.id DESC", (user["id"],)).fetchall()
    out = []
    now = datetime.now(timezone.utc)
    for r in rows:
        d = dict(r)
        exp = datetime.fromisoformat(d["expires_at"])
        if d["status"] == "ACTIVE" and now >= exp: d["status"] = "EXPIRED"
        d["remaining_seconds"] = max(0, int((exp - now).total_seconds()))
        out.append(d)
    return out

@app.get("/api/admin/overview")
def admin_overview(admin: dict = Depends(admin_user)):
    with db() as c:
        return {
            "total_users": c.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0],
            "active_subscriptions": c.execute("SELECT COUNT(*) FROM subscriptions WHERE status='ACTIVE'").fetchone()[0],
            "pending_payments": c.execute("SELECT COUNT(*) FROM orders WHERE status='PENDING'").fetchone()[0],
            "approved_payments": c.execute("SELECT COUNT(*) FROM orders WHERE status='APPROVED'").fetchone()[0],
            "rejected_payments": c.execute("SELECT COUNT(*) FROM orders WHERE status='REJECTED'").fetchone()[0]
        }

@app.get("/api/admin/orders")
def admin_orders(status: Optional[str] = None, admin: dict = Depends(admin_user)):
    q = "SELECT o.*, u.username, p.title as plan_title FROM orders o JOIN users u ON u.id = o.user_id JOIN plans p ON p.id = o.plan_id"
    args = []
    if status: q += " WHERE o.status=?"; args.append(status)
    q += " ORDER BY o.id DESC"
    with db() as c: rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/orders/review")
def admin_review(body: ReviewIn, admin: dict = Depends(admin_user)):
    if body.action not in ("approve", "reject"): raise HTTPException(400, "Bad action")
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_id=?", (body.order_id,)).fetchone()
        if not o or o["status"] != "PENDING": raise HTTPException(400, "Order not pending")
        if body.action == "reject":
            c.execute("UPDATE orders SET status='REJECTED', admin_note=?, reviewed_at=? WHERE order_id=?", (body.admin_note, now_iso(), body.order_id))
            return {"ok": True}
        plan = c.execute("SELECT * FROM plans WHERE id=?", (o["plan_id"],)).fetchone()
        started = datetime.now(timezone.utc)
        expires = started + timedelta(hours=plan["duration_hours"])
        c.execute("UPDATE orders SET status='APPROVED', admin_note=?, reviewed_at=? WHERE order_id=?", (body.admin_note, now_iso(), body.order_id))
        c.execute("INSERT INTO subscriptions (user_id, order_id, plan_id, guild_id, started_at, expires_at, status) VALUES (?,?,?,?,?,?,'ACTIVE')", (o["user_id"], o["order_id"], o["plan_id"], o["guild_id"], started.isoformat(), expires.isoformat()))
    return {"ok": True, "expires_at": expires.isoformat()}

@app.get("/api/admin/plans")
def admin_plans(admin: dict = Depends(admin_user)):
    with db() as c: rows = c.execute("SELECT * FROM plans ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/plans")
def admin_plan_create(body: PlanIn, admin: dict = Depends(admin_user)):
    with db() as c:
        cur = c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES (?,?,?,?,?,?,?)", (body.title, body.description, body.price, body.duration_hours, body.features, body.active, now_iso()))
    return {"id": cur.lastrowid}

@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(admin_user)):
    with db() as c: rows = c.execute("SELECT id, username, role, suspended, created_at FROM users ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/admin/settings")
def admin_settings_get(admin: dict = Depends(admin_user)):
    with db() as c: rows = c.execute("SELECT key, value FROM admin_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}

@app.post("/api/admin/settings")
def admin_settings_set(body: SettingIn, admin: dict = Depends(admin_user)):
    with db() as c: c.execute("INSERT OR REPLACE INTO admin_settings (key, value) VALUES (?,?)", (body.key, body.value))
    return {"ok": True}

@app.get("/api/admin/logs")
def admin_logs(admin: dict = Depends(admin_user)):
    with db() as c: rows = c.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]

@app.get("/", response_class=HTMLResponse)
def index():
    with open("frontend.html", "r", encoding="utf-8") as f: return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
