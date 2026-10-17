import os
import re

# ==========================================
# 1. UPDATE FRONTEND (index.html)
# ==========================================
with open('frontend.html', 'r', encoding='utf-8') as f:
    h = f.read()

# Replace Auth Modal with PIN Modal
new_auth_modal = '''
<div class="modal" id="adminModal">
  <div class="modal-box" style="max-width:600px">
    <div class="modal-h">
      <div class="modal-t" id="adminTitle">ADMIN ACCESS</div>
      <button class="close-x" onclick="closeModal('adminModal')">✕</button>
    </div>
    <div id="adminGate">
      <div class="field"><label>ENTER 4-DIGIT PIN</label><input id="adminPin" type="password" placeholder="****" maxlength="4" style="text-align:center;font-size:24px;letter-spacing:8px;"></div>
      <button class="btn btn-p" onclick="checkPin()">UNLOCK PANEL</button>
      <div class="hint" id="adminHint"></div>
    </div>
    <div id="adminContent" style="display:none">
      <div class="admin-sec">
        <div class="admin-h">Overview</div>
        <div id="ovw"><div class="skeleton"></div></div>
      </div>
      <div class="admin-sec">
        <div class="admin-h">Pending Orders</div>
        <div id="pendingList"></div>
      </div>
      <div class="admin-sec">
        <div class="admin-h">All Orders</div>
        <div id="allOrders"></div>
      </div>
      <div class="admin-sec">
        <div class="admin-h">Plans</div>
        <div id="adminPlans"></div>
        <button class="btn btn-g" onclick="addPlanPrompt()" style="margin-top:8px">+ ADD NEW PLAN</button>
      </div>
      <div class="admin-sec">
        <div class="admin-h">Settings</div>
        <div class="field"><label>UPI ID</label><input id="setUpiId"></div>
        <div class="field"><label>MERCHANT NAME</label><input id="setUpiName"></div>
        <div class="field"><label>DEMO COUNTER MIN</label><input id="setDcMin" type="number"></div>
        <div class="field"><label>DEMO COUNTER MAX</label><input id="setDcMax" type="number"></div>
        <button class="btn btn-p" onclick="saveSettings()">SAVE SETTINGS</button>
      </div>
      <div class="admin-sec">
        <div class="admin-h">Users</div>
        <div id="usersList"></div>
      </div>
      <div class="admin-sec">
        <div class="admin-h">Activity Logs</div>
        <div id="logsList" style="max-height:220px;overflow-y:auto"></div>
      </div>
    </div>
  </div>
</div>
'''

# Remove old auth/checkout modals
h = re.sub(r'<!-- AUTH MODAL -->.*?<!-- CHECKOUT MODAL -->', new_auth_modal + '\n<!-- CHECKOUT MODAL -->', h, flags=re.DOTALL)
h = re.sub(r'<!-- ADMIN MODAL -->.*?<!-- USER MODAL -->', '', h, flags=re.DOTALL)
h = re.sub(r'<!-- USER MODAL -->.*?<div class="toast"', '<div class="toast"', h, flags=re.DOTALL)

# Replace JS
new_js = '''
const API = window.location.origin;
const $ = id => document.getElementById(id);

let currentPlanForCheckout = null;
let currentOrder = null;
let demoMin = 180, demoMax = 260;
let subTimers = [];

function toast(msg, cls = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast on " + cls;
  setTimeout(() => t.className = "toast " + cls, 2600);
}
function openModal(id) { $(id).classList.add("on"); }
function closeModal(id) { $(id).classList.remove("on"); }
document.querySelectorAll(".modal").forEach(m => m.addEventListener("click", e => { if (e.target === m) m.classList.remove("on"); }));

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const r = await fetch(API + path, { ...opts, headers });
  let data;
  try { data = await r.json(); } catch { data = {}; }
  if (!r.ok) throw new Error(data.detail || data.message || ("HTTP " + r.status));
  return data;
}

setTimeout(() => { $("loader").classList.add("hide"); loadPlans(); startDemoCounter(); loadPublicSettings(); }, 2400);

async function loadPublicSettings() {
  try {
    const s = await api("/api/settings/public");
    if (s.demo_counter_min) demoMin = parseInt(s.demo_counter_min);
    if (s.demo_counter_max) demoMax = parseInt(s.demo_counter_max);
  } catch {}
}

async function loadPlans() {
  try {
    const plans = await api("/api/plans");
    const box = $("plansList");
    if (!plans.length) { box.innerHTML = '<div class="empty">NO PLANS</div>'; return; }
    box.innerHTML = plans.map(p => `
      <div class="plan">
        <div class="plan-h">
          <div><div class="plan-t">${esc(p.title)}</div><div class="plan-desc">${esc(p.description)}</div></div>
          <div style="text-align:right"><div class="plan-p">₹${p.price}</div><small style="color:var(--mut);font-size:10px;letter-spacing:1px">${p.duration_hours}H</small></div>
        </div>
        <div class="plan-feats">${p.features.split("|").map(f => `<span class="feat">${esc(f.trim())}</span>`).join("")}</div>
        <button class="btn btn-p" onclick='choosePlan(${JSON.stringify(p).replace(/'/g, "&#39;")})'>CHOOSE PLAN</button>
      </div>
    `).join("");
  } catch (e) { $("plansList").innerHTML = '<div class="empty">FAILED TO LOAD</div>'; }
}

function choosePlan(plan) {
  currentPlanForCheckout = plan;
  $("orderSummary").innerHTML = `
    <div class="row"><div class="k">PLAN</div><div class="v">${esc(plan.title)}</div></div>
    <div class="row"><div class="k">DURATION</div><div class="v">${plan.duration_hours} HOURS</div></div>
    <div class="row"><div class="k">AMOUNT</div><div class="v" style="color:var(--cy)">₹${plan.price}.00</div></div>
  `;
  $("guildId").value = "";
  $("coHint").textContent = "";
  openModal("checkoutModal");
}

$("payBtn").onclick = async () => {
  const gid = $("guildId").value.trim();
  if (gid.length < 4) { $("coHint").textContent = "Enter valid guild ID"; $("coHint").className = "hint err"; return; }
  $("payBtn").disabled = true;
  try {
    const r = await api("/api/orders", { method: "POST", body: JSON.stringify({ plan_id: currentPlanForCheckout.id, guild_id: gid }) });
    currentOrder = r;
    const qr = await api("/api/orders/" + r.order_id + "/qr");
    $("qrImg").src = "data:image/png;base64," + qr.qr_png_base64;
    $("qrAmt").textContent = "₹" + qr.amount;
    $("qrOid").textContent = "ORDER " + r.order_id;
    closeModal("checkoutModal"); openModal("qrModal");
  } catch (e) { $("coHint").textContent = e.message; $("coHint").className = "hint err"; } finally { $("payBtn").disabled = false; }
};

$("paidBtn").onclick = () => { closeModal("qrModal"); openModal("pendingModal"); };

/* ADMIN PIN LOGIC */
function checkPin() {
  const pin = $("adminPin").value;
  if (pin === "9010") {
    $("adminGate").style.display = "none";
    $("adminContent").style.display = "";
    $("adminTitle").textContent = "ADMIN PANEL";
    loadAdminData();
  } else {
    $("adminHint").textContent = "WRONG PIN";
    $("adminHint").className = "hint err";
  }
}
$("adminBtn").onclick = () => { $("adminGate").style.display = ""; $("adminContent").style.display = "none"; openModal("adminModal"); };

async function loadAdminData() {
  try { const o = await api("/api/admin/overview"); $("ovw").innerHTML = `<div class="admin-row"><span class="k">USERS</span><span class="v">${o.total_users}</span></div><div class="admin-row"><span class="k">PENDING</span><span class="v" style="color:var(--yl)">${o.pending_payments}</span></div><div class="admin-row"><span class="k">APPROVED</span><span class="v" style="color:var(--gr)">${o.approved_payments}</span></div>`; } catch {}
  try { const po = await api("/api/admin/orders?status=PENDING"); $("pendingList").innerHTML = po.length ? po.map(o => `<div class="admin-row" style="flex-direction:column;align-items:stretch;gap:8px"><div style="display:flex;justify-content:space-between"><span class="k">${o.order_id}</span><span class="v" style="color:var(--cy)">₹${o.amount}</span></div><div style="font-size:11px;color:var(--mut)">guild: ${o.guild_id} · ${new Date(o.created_at).toLocaleString()}</div><div class="btn-row"><button class="btn btn-p" onclick="reviewOrder('${o.order_id}','approve')">APPROVE</button><button class="btn btn-d" onclick="reviewOrder('${o.order_id}','reject')">REJECT</button></div></div>`).join("") : '<div class="empty">NO PENDING ORDERS</div>'; } catch {}
  try { const all = await api("/api/admin/orders"); $("allOrders").innerHTML = all.length ? all.map(o => `<div class="admin-row"><span class="k">${o.order_id}</span><span class="v" style="color:${o.status==='APPROVED'?'var(--gr)':'var(--pk)'};font-size:10px">${o.status}</span></div>`).join("") : '<div class="empty">NO ORDERS</div>'; } catch {}
  try { const plans = await api("/api/admin/plans"); $("adminPlans").innerHTML = plans.map(p => `<div class="admin-row"><span class="k">${esc(p.title)} · ₹${p.price}</span></div>`).join(""); } catch {}
  try { const s = await api("/api/admin/settings"); $("setUpiId").value = s.upi_id||""; $("setUpiName").value = s.upi_name||""; $("setDcMin").value = s.demo_counter_min||""; $("setDcMax").value = s.demo_counter_max||""; } catch {}
  try { const logs = await api("/api/admin/logs"); $("logsList").innerHTML = logs.slice(0,50).map(l => `<div style="padding:6px 0;border-bottom:1px solid var(--line);font-size:11px"><span style="color:var(--mut)">${new Date(l.created_at).toLocaleTimeString()}</span> <strong>${esc(l.actor)}</strong> ${esc(l.action)}</div>`).join(""); } catch {}
}

window.reviewOrder = async (oid, action) => {
  try {
    await api("/api/admin/orders/review", { method: "POST", body: JSON.stringify({ order_id: oid, action, admin_note: "" }) });
    toast("Order " + action + "d", "ok"); loadAdminData();
  } catch (e) { toast(e.message, "err"); }
};

window.saveSettings = async () => {
  try {
    await api("/api/admin/settings", { method: "POST", body: JSON.stringify({key:"upi_id",value:$("setUpiId").value}) });
    await api("/api/admin/settings", { method: "POST", body: JSON.stringify({key:"upi_name",value:$("setUpiName").value}) });
    await api("/api/admin/settings", { method: "POST", body: JSON.stringify({key:"demo_counter_min",value:$("setDcMin").value}) });
    await api("/api/admin/settings", { method: "POST", body: JSON.stringify({key:"demo_counter_max",value:$("setDcMax").value}) });
    toast("Settings saved", "ok");
  } catch (e) { toast(e.message, "err"); }
};

const feedPool = [{ico:"⚔",txt:"MATCH STARTED",detail:"+48 GLORY"},{ico:"👥",txt:"PLAYER JOINED",detail:""},{ico:"✓",txt:"MATCH COMPLETED",detail:"+72 GLORY"},{ico:"📈",txt:"GLORY UPDATED",detail:"+12"}];
function addFeedItem(prepend=false) {
  const box = $("feed"); const item = feedPool[Math.floor(Math.random()*feedPool.length)];
  const el = document.createElement("div"); el.className = "feed-item";
  el.innerHTML = `<div class="feed-icon">${item.ico}</div><div class="feed-txt"><strong>${item.txt}</strong> <span style="color:var(--cy)">${item.detail}</span> <span class="feed-demo">DEMO</span><br><span>${new Date().toLocaleTimeString()}</span></div>`;
  if (prepend && box.firstChild) box.insertBefore(el, box.firstChild); else box.appendChild(el);
  while (box.children.length > 12) box.removeChild(box.lastChild);
}
for(let i=0; i<6; i++) addFeedItem();
setInterval(() => addFeedItem(true), 3200);

function startDemoCounter() {
  const el = $("statMatches"); let cur = Math.floor((demoMin + demoMax) / 2);
  setInterval(() => { cur += Math.floor(Math.random()*7)-3; if(cur<demoMin)cur=demoMin+2; if(cur>demoMax)cur=demoMax-2; el.textContent = cur + "+"; }, 2800);
}
function esc(s) { return String(s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}[c])); }
'''

# Replace old script with new script
h = re.sub(r'<script>.*?</script>', '<script>\n' + new_js + '\n</script>', h, flags=re.DOTALL)

with open('frontend.html', 'w', encoding='utf-8') as f:
    f.write(h)
print("Frontend updated with PIN 9010!")

os.system('git add .')
os.system('git commit -m "Removed user auth, added PIN admin"')
os.system('git push origin main -f')
