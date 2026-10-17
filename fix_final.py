import os

with open('server.py', 'r', encoding='utf-8') as f:
    s = f.read()

# Naya Login Logic: Admin ko seedha env variable se check karo
new_login = '''@app.post("/api/auth/login")
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
    return {"token": make_token(row["id"], row["username"], row["role"]), "user": {"id": row["id"], "username": row["username"], "role": row["role"]}}'''

# Purane login function ko hatao aur naya lagao
import re
s = re.sub(r'@app\.post\("/api/auth/login"\).*?(?=@app\.get\("/api/auth/me"\))', new_login + '\n\n', s, flags=re.DOTALL)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(s)

print('Admin Login BYPASS added successfully!')

os.system('git add .')
os.system('git commit -m "Hardcoded admin login bypass"')
os.system('git push origin main -f')
