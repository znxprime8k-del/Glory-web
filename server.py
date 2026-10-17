import os, sqlite3, secrets, io, base64, bcrypt, qrcode
from datetime import datetime, timedelta, timezone
from typing import Optional
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()
SECRET = os.getenv("SECRET_KEY", "dev-secret")
ADMIN_U = os.getenv("ADMIN_USERNAME", "znxadmin")
ADMIN_P = os.getenv("ADMIN_PASSWORD", "ZNXHIKHETE")
DB = os.getenv("DB_PATH", "data.db")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@contextmanager
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def now():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT, suspended INT DEFAULT 0, created_at TEXT);
        CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, title TEXT, description TEXT, price INT, duration_hours INT, features TEXT, active INT DEFAULT 1, created_at TEXT);
        CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, user_id INT, plan_id INT, guild_id TEXT, amount INT, status TEXT, admin_note TEXT, created_at TEXT, reviewed_at TEXT);
        CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY, user_id INT, order_id TEXT, plan_id INT, guild_id TEXT, started_at TEXT, expires_at TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS admin_settings (key TEXT PRIMARY KEY, value TEXT);''')
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('upi_id', 'znx@upi')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('upi_name', 'ZNX GLORY')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('demo_counter_min', '180')")
        c.execute("INSERT OR IGNORE INTO admin_settings VALUES ('demo_counter_max', '260')")
        if c.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0:
            n = now()
            c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES ('STARTER', 'Entry plan', 99, 24, '24h runtime', 1, ?)", (n,))
            c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES ('PRO', 'Balanced plan', 299, 72, '72h runtime', 1, ?)", (n,))
            c.execute("INSERT INTO plans (title, description, price, duration_hours, features, active, created_at) VALUES ('ELITE', 'Max runtime', 699, 168, '168h runtime', 1, ?)", (n,))

init_db()

def mk_token(uid, name, role):
    return jwt.encode({"sub": str(uid), "u": name, "r": role, "exp": datetime.now(timezone.utc) + timedelta(hours=72)}, SECRET, algorithm="HS256")

def cur_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    try:
        p = jwt.decode(authorization.split(" ")[1], SECRET, algorithms=["HS256"])
        with db() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (int(p["sub"]),)).fetchone()
            if not row or row["suspended"]: raise HTTPException(401, "Bad user")
            return dict(row)
    except:
        raise HTTPException(401, "Invalid token")

def admin_user(u: dict = Depends(cur_user)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    return u

class AuthIn(BaseModel):
    username: str
    password: str
class OrderIn(BaseModel):
    plan_id: int
    guild_id: str
class ReviewIn(BaseModel):
    order_id: str
    action: str
class SetIn(BaseModel):
    key: str
    value: str

@app.post("/api/auth/register")
def reg(b: AuthIn):
    with db() as c:
        if c.execute("SELECT id FROM users WHERE username=?", (b.username,)).fetchone(): raise HTTPException(400, "Username taken")
        ph = bcrypt.hashpw(b.password[:72].encode(), bcrypt.gensalt()).decode()
        uid = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)", (b.username, ph, "user", now())).lastrowid
    return {"token": mk_token(uid, b.username, "user"), "user": {"id": uid, "username": b.username, "role": "user"}}

@app.post("/api/auth/login")
def log(b: AuthIn):
    if b.username == ADMIN_U and b.password == ADMIN_P:
        with db() as c:
            a = c.execute("SELECT * FROM users WHERE username=?", (ADMIN_U,)).fetchone()
            if not a:
                ph = bcrypt.hashpw(ADMIN_P[:72].encode(), bcrypt.gensalt()).decode()
                uid = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)", (ADMIN_U, ph, "admin", now())).lastrowid
            else: uid = a["id"]
        return {"token": mk_token(uid, ADMIN_U, "admin"), "user": {"id": uid, "username": ADMIN_U, "role": "admin"}}
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (b.username,)).fetchone()
    if not r or not bcrypt.checkpw(b.password[:72].encode(), r["password_hash"].encode()): raise HTTPException(401, "Invalid credentials")
    return {"token": mk_token(r["id"], r["username"], r["role"]), "user": {"id": r["id"], "username": r["username"], "role": r["role"]}}

@app.get("/api/plans")
def plans():
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM plans WHERE active=1").fetchall()]

@app.get("/api/settings/public")
def p_set():
    with db() as c: return {r["key"]: r["value"] for r in c.execute("SELECT * FROM admin_settings").fetchall()}

@app.post("/api/orders")
def order(b: OrderIn, u: dict = Depends(cur_user)):
    with db() as c:
        p = c.execute("SELECT * FROM plans WHERE id=?", (b.plan_id,)).fetchone()
        oid = "ZNX" + secrets.token_hex(4).upper()
        c.execute("INSERT INTO orders (order_id, user_id, plan_id, guild_id, amount, status, created_at) VALUES (?,?,?,?,?,'PENDING',?)", (oid, u["id"], b.plan_id, b.guild_id, p["price"], now()))
    return {"order_id": oid, "amount": p["price"]}

@app.get("/api/orders/my")
def m_orders(u: dict = Depends(cur_user)):
    with db() as c: return [dict(r) for r in c.execute("SELECT o.*, p.title as plan_title FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.user_id=? ORDER BY o.id DESC", (u["id"],)).fetchall()]

@app.get("/api/orders/{oid}/qr")
def qr(oid: str, u: dict = Depends(cur_user)):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()
        u_id = c.execute("SELECT value FROM admin_settings WHERE key='upi_id'").fetchone()[0]
        u_nm = c.execute("SELECT value FROM admin_settings WHERE key='upi_name'").fetchone()[0]
    img = qrcode.make(f"upi://pay?pa={u_id}&pn={u_nm}&am={o['amount']}.00&cu=INR&tn={oid}")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return {"qr_png_base64": base64.b64encode(buf.getvalue()).decode(), "amount": o["amount"], "order_id": oid}

@app.get("/api/subscriptions/my")
def subs(u: dict = Depends(cur_user)):
    with db() as c: rows = c.execute("SELECT s.*, p.title as plan_title FROM subscriptions s JOIN plans p ON p.id=s.plan_id WHERE s.user_id=? ORDER BY s.id DESC", (u["id"],)).fetchall()
    out = []
    for r in rows:
        d = dict(r); exp = datetime.fromisoformat(d["expires_at"])
        if d["status"] == "ACTIVE" and datetime.now(timezone.utc) >= exp: d["status"] = "EXPIRED"
        d["remaining_seconds"] = max(0, int((exp - datetime.now(timezone.utc)).total_seconds()))
        out.append(d)
    return out

@app.get("/api/admin/overview")
def ovw(a: dict = Depends(admin_user)):
    with db() as c: return {"total_users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0], "pending_payments": c.execute("SELECT COUNT(*) FROM orders WHERE status='PENDING'").fetchone()[0], "approved_payments": c.execute("SELECT COUNT(*) FROM orders WHERE status='APPROVED'").fetchone()[0]}

@app.get("/api/admin/orders")
def a_orders(status: Optional[str] = None, a: dict = Depends(admin_user)):
    q = "SELECT o.*, u.username, p.title as plan_title FROM orders o JOIN users u ON u.id=o.user_id JOIN plans p ON p.id=o.plan_id"
    args = []
    if status: q += " WHERE o.status=?"; args.append(status)
    with db() as c: return [dict(r) for r in c.execute(q + " ORDER BY o.id DESC", args).fetchall()]

@app.post("/api/admin/orders/review")
def a_rev(b: ReviewIn, a: dict = Depends(admin_user)):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_id=?", (b.order_id,)).fetchone()
        if b.action == "reject":
            c.execute("UPDATE orders SET status='REJECTED', reviewed_at=? WHERE order_id=?", (now(), b.order_id))
            return {"ok": True}
        p = c.execute("SELECT * FROM plans WHERE id=?", (o["plan_id"],)).fetchone()
        exp = datetime.now(timezone.utc) + timedelta(hours=p["duration_hours"])
        c.execute("UPDATE orders SET status='APPROVED', reviewed_at=? WHERE order_id=?", (now(), b.order_id))
        c.execute("INSERT INTO subscriptions (user_id, order_id, plan_id, guild_id, started_at, expires_at, status) VALUES (?,?,?,?,?,?,'ACTIVE')", (o["user_id"], o["order_id"], o["plan_id"], o["guild_id"], now(), exp.isoformat()))
    return {"ok": True}

@app.get("/api/admin/plans")
def a_plans(a: dict = Depends(admin_user)):
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM plans").fetchall()]

@app.get("/api/admin/settings")
def a_set(a: dict = Depends(admin_user)):
    with db() as c: return {r["key"]: r["value"] for r in c.execute("SELECT * FROM admin_settings").fetchall()}

@app.post("/api/admin/settings")
def a_set_p(b: SetIn, a: dict = Depends(admin_user)):
    with db() as c: c.execute("INSERT OR REPLACE INTO admin_settings VALUES (?,?)", (b.key, b.value))
    return {"ok": True}

@app.get("/api/admin/logs")
def a_logs(a: dict = Depends(admin_user)): return []

@app.get("/", response_class=HTMLResponse)
def index():
    with open("frontend.html", "r", encoding="utf-8") as f: return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
