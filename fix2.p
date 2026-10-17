import os

with open('server.py', 'r', encoding='utf-8') as f:
    s = f.read()

# Remove passlib import
s = s.replace('from passlib.context import CryptContext', 'import bcrypt')

# Remove passlib initialization
s = s.replace('pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")', '')

# Fix hashing and verification
s = s.replace('ph = pwd_ctx.hash(ADMIN_PASSWORD)', 'ph = bcrypt.hashpw(ADMIN_PASSWORD[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")')
s = s.replace('ph = pwd_ctx.hash(body.password)', 'ph = bcrypt.hashpw(body.password[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")')
s = s.replace('if not row or not pwd_ctx.verify(body.password, row["password_hash"]):', 'if not row or not bcrypt.checkpw(body.password[:72].encode("utf-8"), row["password_hash"].encode("utf-8")):')

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(s)
print('Fixed bcrypt issue!')

os.system('git add .')
os.system('git commit -m "Fixed bcrypt password limit"')
os.system('git push origin main')
