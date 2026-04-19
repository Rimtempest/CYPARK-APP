from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from flask_socketio import SocketIO, emit
import json, os, time, random, hashlib, uuid, sqlite3, csv, io
from datetime import datetime, timedelta
from collections import deque
import qrcode
import base64
from functools import wraps

app = Flask(__name__)
app.secret_key = 'cypark_pro_secret_2025'
socketio = SocketIO(app, cors_allowed_origins="*")

SETTINGS = {
    'rate_per_hour': 40,
    'penalty_per_hour': 20,
    'max_stay_hours': 24,
    'slot_count': 30,
    'discount_senior': 0.20,
    'discount_pwd': 0.20,
    'emergency_mode': False,
    'grace_period_minutes': 15,
    'brute_force_limit': 5,
    'reservation_fee': 50,
}
SETTINGS_FILE = 'data/settings.json'

def load_settings():
    global SETTINGS
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            SETTINGS.update(json.load(f))

def save_settings():
    os.makedirs('data', exist_ok=True)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(SETTINGS, f, indent=2)

load_settings()

DB_PATH = 'data/cypark.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs('data', exist_ok=True)
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            role TEXT DEFAULT 'user',
            created TEXT,
            blocked INTEGER DEFAULT 0,
            failed_logins INTEGER DEFAULT 0,
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS slots (
            slot_id TEXT PRIMARY KEY,
            floor TEXT,
            zone TEXT,
            occupied INTEGER DEFAULT 0,
            plate TEXT,
            session_id TEXT,
            entry_time TEXT,
            slot_type TEXT DEFAULT 'regular',
            reserved INTEGER DEFAULT 0,
            reserved_by TEXT,
            reserved_until TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            slot_id TEXT,
            plate TEXT,
            username TEXT,
            entry TEXT,
            exit TEXT,
            fee REAL,
            discount_type TEXT DEFAULT 'none',
            status TEXT DEFAULT 'active',
            qr_data TEXT,
            invalid_qr_attempts INTEGER DEFAULT 0,
            paid_before_exit INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY,
            username TEXT,
            plate TEXT,
            slot_id TEXT,
            reserved_at TEXT,
            expires_at TEXT,
            status TEXT DEFAULT 'active',
            fee REAL DEFAULT 50
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            slot_id TEXT,
            plate TEXT,
            session_id TEXT,
            amount REAL DEFAULT 0,
            username TEXT,
            created TEXT
        );
        CREATE TABLE IF NOT EXISTS queue (
            id TEXT PRIMARY KEY,
            username TEXT,
            plate TEXT,
            joined_at TEXT,
            status TEXT DEFAULT 'waiting'
        );
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            plate TEXT,
            hours REAL,
            rate REAL,
            discount_pct REAL,
            discount_type TEXT,
            subtotal REAL,
            discount_amount REAL,
            penalty REAL,
            total REAL,
            created TEXT,
            username TEXT,
            paid_before_exit INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS violations (
            id TEXT PRIMARY KEY,
            plate TEXT,
            slot_id TEXT,
            violation_type TEXT,
            detail TEXT,
            created TEXT,
            resolved INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            username TEXT,
            message TEXT,
            type TEXT DEFAULT 'info',
            created TEXT,
            read INTEGER DEFAULT 0
        );
        INSERT OR IGNORE INTO users (username, password, name, email, role, created, blocked)
        VALUES ('admin', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
                'Administrator', 'admin@cypark.ph', 'admin', datetime('now'), 0);
    ''')
    # SM Fairview-style parking layout: A=Ground, B=Level 2, C=Rooftop
    floors = ['A', 'B', 'C']
    zones = ['North', 'South']
    for f in floors:
        for z in zones:
            for s in range(1, 6):
                sid = f"{f}{z[0]}-{s:02d}"
                c.execute("INSERT OR IGNORE INTO slots (slot_id, floor, zone) VALUES (?,?,?)", (sid, f, z))
    conn.commit()
    conn.close()

init_db()

class Stack:
    def __init__(self):
        self._items = []
    def push(self, item): self._items.append(item)
    def pop(self): return self._items.pop() if self._items else None
    def peek(self): return self._items[-1] if self._items else None
    def is_empty(self): return len(self._items) == 0
    def size(self): return len(self._items)
    def to_list(self): return list(reversed(self._items))

class Queue:
    def __init__(self):
        self._items = deque()
    def enqueue(self, item): self._items.append(item)
    def dequeue(self): return self._items.popleft() if self._items else None
    def peek(self): return self._items[0] if self._items else None
    def is_empty(self): return len(self._items) == 0
    def size(self): return len(self._items)
    def to_list(self): return list(self._items)

transaction_stack = Stack()
waiting_queue = Queue()

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'ok': False, 'msg': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'error': 'Unauthorized'}), 403
        return f(*args, **kwargs)
    return decorated

def generate_qr(text):
    qr = qrcode.QRCode(version=1, box_size=8, border=3)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#FFFFFF", back_color="#0A0F1E")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()

def make_qr_data(session_id, slot_id, plate, entry_time):
    return f"CYPARK|{session_id}|{slot_id}|{plate}|{entry_time}"

def parse_qr(qr_string):
    try:
        parts = qr_string.strip().split('|')
        if len(parts) == 5 and parts[0] == 'CYPARK':
            return {'session_id': parts[1], 'slot_id': parts[2], 'plate': parts[3], 'entry': parts[4]}
    except:
        pass
    return None

def recommend_slot(preference='nearest_exit'):
    conn = get_db()
    slots = conn.execute("SELECT * FROM slots WHERE occupied=0 AND reserved=0").fetchall()
    conn.close()
    if not slots:
        return None, None
    slot_list = [dict(s) for s in slots]
    floor_order = {'A': 0, 'B': 1, 'C': 2}
    if preference == 'nearest_exit':
        slot_list.sort(key=lambda s: (floor_order.get(s['floor'], 9), s['zone'], s['slot_id']))
    elif preference == 'least_crowded':
        conn = get_db()
        floor_counts = {}
        for f in ['A','B','C']:
            cnt = conn.execute("SELECT COUNT(*) FROM slots WHERE floor=? AND occupied=1",(f,)).fetchone()[0]
            floor_counts[f] = cnt
        conn.close()
        slot_list.sort(key=lambda s: (floor_counts.get(s['floor'],0), s['slot_id']))
    best = slot_list[0]
    floor_path = {
        'A': 'Ground Level — Enter main gate, proceed straight',
        'B': 'Level 2 — Take ramp at entrance, turn right',
        'C': 'Rooftop — Take ramp to top level, open parking'
    }
    path = floor_path.get(best['floor'], 'Follow directional signs')
    return best['slot_id'], path

def add_notification(username, msg, ntype='info'):
    conn = get_db()
    conn.execute("INSERT INTO notifications (id, username, message, type, created) VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4())[:8], username, msg, ntype, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    socketio.emit('notification', {'user': username, 'msg': msg, 'type': ntype})

def get_analytics(period='week'):
    conn = get_db()
    now = datetime.now()
    total_slots = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    occupied = conn.execute("SELECT COUNT(*) FROM slots WHERE occupied=1").fetchone()[0]
    today = now.strftime('%Y-%m-%d')
    rev_today = conn.execute("SELECT COALESCE(SUM(total),0) FROM payments WHERE DATE(created)=?", (today,)).fetchone()[0]
    revenue_chart = []
    if period == 'week':
        for i in range(7):
            day = (now - timedelta(days=6-i))
            d_str = day.strftime('%Y-%m-%d')
            amt = conn.execute("SELECT COALESCE(SUM(total),0) FROM payments WHERE DATE(created)=?", (d_str,)).fetchone()[0]
            revenue_chart.append({'label': day.strftime('%a'), 'amount': round(float(amt), 2)})
    elif period == 'month':
        for i in range(30):
            day = (now - timedelta(days=29-i))
            d_str = day.strftime('%Y-%m-%d')
            amt = conn.execute("SELECT COALESCE(SUM(total),0) FROM payments WHERE DATE(created)=?", (d_str,)).fetchone()[0]
            revenue_chart.append({'label': day.strftime('%d'), 'amount': round(float(amt), 2)})
    hourly = {}
    rows = conn.execute("SELECT strftime('%H', created) as hr, COUNT(*) as cnt FROM sessions GROUP BY hr").fetchall()
    for r in rows:
        hourly[int(r['hr'])] = r['cnt']
    peak_hour = max(hourly, key=hourly.get) if hourly else 10
    heatmap = {}
    slot_rows = conn.execute("SELECT slot_id, COUNT(*) as cnt FROM sessions GROUP BY slot_id").fetchall()
    for r in slot_rows:
        heatmap[r['slot_id']] = r['cnt']
    avg_dur = conn.execute("SELECT AVG((julianday(exit)-julianday(entry))*24) FROM sessions WHERE status='closed' AND exit IS NOT NULL").fetchone()[0]
    total_rev = conn.execute("SELECT COALESCE(SUM(total),0) FROM payments").fetchone()[0]
    rev_monthly = []
    for i in range(12):
        m = (now.replace(day=1) - timedelta(days=i*28)).strftime('%Y-%m')
        amt = conn.execute("SELECT COALESCE(SUM(total),0) FROM payments WHERE strftime('%Y-%m',created)=?", (m,)).fetchone()[0]
        rev_monthly.insert(0, {'label': m, 'amount': round(float(amt), 2)})
    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    active_sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='active'").fetchone()[0]
    violations_count = conn.execute("SELECT COUNT(*) FROM violations WHERE resolved=0").fetchone()[0]
    conn.close()
    return {
        'total': total_slots, 'occupied': occupied, 'vacant': total_slots - occupied,
        'occupancy_rate': round(occupied/total_slots*100, 1) if total_slots else 0,
        'revenue_today': round(float(rev_today), 2),
        'revenue_chart': revenue_chart,
        'peak_hour': peak_hour,
        'hourly': hourly,
        'heatmap': heatmap,
        'avg_duration': round(float(avg_dur or 0), 2),
        'total_revenue': round(float(total_rev), 2),
        'revenue_monthly': rev_monthly,
        'total_sessions': total_sessions,
        'active_sessions': active_sessions,
        'violations': violations_count,
        'queue_size': waiting_queue.size(),
    }

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session: return redirect('/')
    return render_template('dashboard.html', user=session['name'], role=session['role'], username=session['user'])

@app.route('/admin')
def admin_page():
    if session.get('role') != 'admin': return redirect('/dashboard')
    return render_template('admin.html', user=session['name'])

@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json
    u = d.get('username','').strip().lower()
    p = d.get('password','')
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invalid credentials'})
    if user['blocked']:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Account suspended. Contact administrator.'})
    failed = user['failed_logins'] or 0
    if failed >= SETTINGS['brute_force_limit']:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Account locked after {SETTINGS["brute_force_limit"]} failed attempts.'})
    if user['password'] != hash_pw(p):
        conn.execute("UPDATE users SET failed_logins=failed_logins+1 WHERE username=?", (u,))
        conn.commit()
        conn.close()
        remaining = SETTINGS['brute_force_limit'] - failed - 1
        return jsonify({'ok': False, 'msg': f'Invalid credentials. {remaining} attempts remaining.'})
    conn.execute("UPDATE users SET failed_logins=0, last_login=? WHERE username=?", (datetime.now().isoformat(), u))
    conn.commit()
    conn.close()
    session['user'] = u
    session['role'] = user['role']
    session['name'] = user['name']
    session.permanent = True
    return jsonify({'ok': True, 'role': user['role'], 'name': user['name']})

@app.route('/api/register', methods=['POST'])
def api_register():
    d = request.json
    username = d.get('username','').strip().lower()
    if not username or not d.get('password') or not d.get('name'):
        return jsonify({'ok': False, 'msg': 'All fields required'})
    conn = get_db()
    exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Username already taken'})
    conn.execute("INSERT INTO users (username,password,name,email,role,created) VALUES (?,?,?,?,?,?)",
                 (username, hash_pw(d['password']), d['name'], d.get('email',''), 'user', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    add_notification(username, f"Welcome to CYPARK, {d['name']}! Your account is ready.", 'success')
    return jsonify({'ok': True})

@app.route('/api/logout')
def api_logout():
    session.clear()
    return redirect('/')

@app.route('/api/slots')
def api_slots():
    conn = get_db()
    slots = [dict(r) for r in conn.execute("SELECT * FROM slots ORDER BY slot_id").fetchall()]
    conn.close()
    return jsonify(slots)

@app.route('/api/recommend')
@login_required
def api_recommend():
    pref = request.args.get('pref', 'nearest_exit')
    slot_id, path = recommend_slot(pref)
    conn = get_db()
    stats = {}
    for f in ['A','B','C']:
        total = conn.execute("SELECT COUNT(*) FROM slots WHERE floor=?", (f,)).fetchone()[0]
        occ = conn.execute("SELECT COUNT(*) FROM slots WHERE floor=? AND occupied=1", (f,)).fetchone()[0]
        stats[f] = {'total': total, 'occupied': occ, 'vacant': total-occ}
    conn.close()
    return jsonify({'slot': slot_id, 'path': path, 'floor_stats': stats})

# ── RESERVATION ──────────────────────────────────────────────
@app.route('/api/reserve', methods=['POST'])
@login_required
def api_reserve():
    d = request.json
    plate = d.get('plate','').upper().strip()
    slot_id = d.get('slot_id') or None

    if not plate:
        return jsonify({'ok': False, 'msg': 'Plate number required'})

    conn = get_db()
    # Check if user already has active reservation
    existing_res = conn.execute(
        "SELECT id FROM reservations WHERE username=? AND status='active'",
        (session['user'],)
    ).fetchone()
    if existing_res:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have an active reservation'})

    if slot_id:
        slot = conn.execute("SELECT * FROM slots WHERE slot_id=? AND occupied=0 AND reserved=0", (slot_id,)).fetchone()
        if not slot:
            conn.close()
            return jsonify({'ok': False, 'msg': 'Slot unavailable or already reserved'})
    else:
        # Auto pick nearest
        slot_id, _ = recommend_slot()
        if not slot_id:
            conn.close()
            return jsonify({'ok': False, 'msg': 'No vacant slots available'})

    res_id = str(uuid.uuid4())[:12].upper()
    now = datetime.now()
    expires = now + timedelta(minutes=30)
    fee = SETTINGS.get('reservation_fee', 50)

    conn.execute("UPDATE slots SET reserved=1, reserved_by=?, reserved_until=? WHERE slot_id=?",
                 (session['user'], expires.isoformat(), slot_id))
    conn.execute("INSERT INTO reservations (id,username,plate,slot_id,reserved_at,expires_at,status,fee) VALUES (?,?,?,?,?,?,?,?)",
                 (res_id, session['user'], plate, slot_id, now.isoformat(), expires.isoformat(), 'active', fee))
    conn.commit()
    conn.close()

    # Generate QR for reservation
    qr_data = f"CYPARK-RES|{res_id}|{slot_id}|{plate}|{now.isoformat()}"
    qr_img = generate_qr(qr_data)

    add_notification(session['user'], f'Slot {slot_id} reserved for {plate}. Valid 30 minutes. Fee: P{fee}', 'success')
    socketio.emit('slot_update', {'slot_id': slot_id, 'occupied': False, 'reserved': True, 'plate': plate})

    return jsonify({
        'ok': True,
        'reservation_id': res_id,
        'slot_id': slot_id,
        'plate': plate,
        'expires_at': expires.isoformat(),
        'fee': fee,
        'qr': qr_img,
        'qr_data': qr_data
    })

@app.route('/api/reserve/cancel', methods=['POST'])
@login_required
def api_reserve_cancel():
    res_id = request.json.get('reservation_id','').upper()
    conn = get_db()
    res = conn.execute("SELECT * FROM reservations WHERE id=? AND username=? AND status='active'",
                       (res_id, session['user'])).fetchone()
    if not res:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Reservation not found'})
    res = dict(res)
    conn.execute("UPDATE reservations SET status='cancelled' WHERE id=?", (res_id,))
    conn.execute("UPDATE slots SET reserved=0, reserved_by=NULL, reserved_until=NULL WHERE slot_id=?", (res['slot_id'],))
    conn.commit()
    conn.close()
    socketio.emit('slot_update', {'slot_id': res['slot_id'], 'occupied': False, 'reserved': False})
    return jsonify({'ok': True})

@app.route('/api/my_reservations')
@login_required
def api_my_reservations():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM reservations WHERE username=? ORDER BY reserved_at DESC LIMIT 10",
        (session['user'],)).fetchall()]
    conn.close()
    return jsonify(rows)

# ── PARK IN ──────────────────────────────────────────────────
@app.route('/api/park', methods=['POST'])
@login_required
def api_park():
    if SETTINGS.get('emergency_mode'):
        return jsonify({'ok': False, 'msg': 'EMERGENCY MODE ACTIVE — Entry disabled'})

    d = request.json
    plate = d.get('plate','').upper().strip()
    slot_id = d.get('slot_id') or None
    discount_type = d.get('discount_type', 'none')

    if not plate:
        return jsonify({'ok': False, 'msg': 'Plate number required'})

    conn = get_db()
    existing = conn.execute("SELECT session_id FROM sessions WHERE plate=? AND status='active'", (plate,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Plate {plate} already has an active session'})

    # Check if user has a reservation for this plate
    reservation = conn.execute(
        "SELECT * FROM reservations WHERE username=? AND plate=? AND status='active'",
        (session['user'], plate)
    ).fetchone()

    if reservation:
        reservation = dict(reservation)
        slot_id = reservation['slot_id']
        # Mark reservation as used
        conn.execute("UPDATE reservations SET status='used' WHERE id=?", (reservation['id'],))

    if slot_id:
        slot = conn.execute("SELECT * FROM slots WHERE slot_id=? AND occupied=0", (slot_id,)).fetchone()
        if not slot:
            conn.close()
            return jsonify({'ok': False, 'msg': 'Slot unavailable or already occupied'})
    else:
        slot_id, _ = recommend_slot()
        if not slot_id:
            conn.close()
            return jsonify({'ok': False, 'msg': 'No vacant slots available'})

    session_id = str(uuid.uuid4())[:12].upper()
    now = datetime.now().isoformat()
    qr_data = make_qr_data(session_id, slot_id, plate, now)
    qr_img = generate_qr(qr_data)

    conn.execute("UPDATE slots SET occupied=1, plate=?, session_id=?, entry_time=?, reserved=0, reserved_by=NULL, reserved_until=NULL WHERE slot_id=?",
                 (plate, session_id, now, slot_id))
    conn.execute("INSERT INTO sessions (session_id,slot_id,plate,username,entry,status,qr_data,discount_type) VALUES (?,?,?,?,?,?,?,?)",
                 (session_id, slot_id, plate, session['user'], now, 'active', qr_data, discount_type))
    conn.execute("INSERT INTO transactions (type,slot_id,plate,session_id,username,created) VALUES (?,?,?,?,?,?)",
                 ('PARK_IN', slot_id, plate, session_id, session['user'], now))
    conn.commit()
    conn.close()

    transaction_stack.push({'type':'PARK_IN','slot':slot_id,'plate':plate,'session':session_id,'time':now})
    add_notification(session['user'], f'{plate} parked at {slot_id}', 'success')
    socketio.emit('slot_update', {'slot_id': slot_id, 'occupied': True, 'plate': plate})
    socketio.emit('analytics', get_analytics())

    return jsonify({
        'ok': True,
        'session_id': session_id,
        'slot_id': slot_id,
        'qr': qr_img,
        'entry': now,
        'qr_data': qr_data,
        'plate': plate
    })

# ── PAY BEFORE EXIT ──────────────────────────────────────────
@app.route('/api/pay_before_exit', methods=['POST'])
@login_required
def api_pay_before_exit():
    d = request.json
    session_id = d.get('session_id','').upper().strip()
    discount_override = d.get('discount_type')

    conn = get_db()
    sess = conn.execute("SELECT * FROM sessions WHERE session_id=? AND status='active'", (session_id,)).fetchone()
    if not sess:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invalid or already closed session'})

    sess = dict(sess)
    if sess.get('paid_before_exit'):
        conn.close()
        return jsonify({'ok': False, 'msg': 'Already paid. Please proceed to exit.'})

    entry = datetime.fromisoformat(sess['entry'])
    now = datetime.now()
    hours = max((now - entry).total_seconds() / 3600, 0.5)
    rate = SETTINGS['rate_per_hour']
    subtotal = hours * rate
    disc_type = discount_override or sess.get('discount_type','none')
    disc_pct = 0
    if disc_type == 'senior': disc_pct = SETTINGS['discount_senior']
    elif disc_type == 'pwd': disc_pct = SETTINGS['discount_pwd']
    disc_amount = subtotal * disc_pct
    penalty = 0
    if hours > SETTINGS['max_stay_hours']:
        overstay_hours = hours - SETTINGS['max_stay_hours']
        penalty = overstay_hours * SETTINGS['penalty_per_hour']
    total = max(subtotal - disc_amount + penalty, SETTINGS['rate_per_hour'] * 0.5)
    payment_id = str(uuid.uuid4())[:12].upper()

    conn.execute("UPDATE sessions SET paid_before_exit=1, discount_type=? WHERE session_id=?",
                 (disc_type, session_id))
    conn.execute("""INSERT INTO payments (id,session_id,plate,hours,rate,discount_pct,discount_type,
                    subtotal,discount_amount,penalty,total,created,username,paid_before_exit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (payment_id, session_id, sess['plate'], round(hours,2), rate, disc_pct, disc_type,
                  round(subtotal,2), round(disc_amount,2), round(penalty,2), round(total,2),
                  now.isoformat(), session['user'], 1))
    conn.commit()
    conn.close()

    add_notification(session['user'], f'Payment confirmed for {sess["plate"]}. P{total:.2f}. You may exit anytime.', 'success')

    return jsonify({
        'ok': True,
        'payment_id': payment_id,
        'fee': round(total,2),
        'hours': round(hours,2),
        'plate': sess['plate'],
        'slot_id': sess['slot_id'],
        'subtotal': round(subtotal,2),
        'discount_amount': round(disc_amount,2),
        'discount_type': disc_type,
        'penalty': round(penalty,2),
        'rate': rate
    })

# ── EXIT ─────────────────────────────────────────────────────
@app.route('/api/exit', methods=['POST'])
@login_required
def api_exit():
    d = request.json
    session_id = d.get('session_id','').upper().strip()
    discount_override = d.get('discount_type')

    conn = get_db()
    sess = conn.execute("SELECT * FROM sessions WHERE session_id=? AND status='active'", (session_id,)).fetchone()
    if not sess:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invalid or already closed session'})

    sess = dict(sess)
    already_paid = sess.get('paid_before_exit', 0)

    entry = datetime.fromisoformat(sess['entry'])
    now = datetime.now()
    hours = max((now - entry).total_seconds() / 3600, 0.5)

    if already_paid:
        # Fetch existing payment
        p = conn.execute("SELECT * FROM payments WHERE session_id=? AND paid_before_exit=1",
                         (session_id,)).fetchone()
        if p:
            p = dict(p)
            total = p['total']
            payment_id = p['id']
        else:
            total = 0
            payment_id = 'ALREADY_PAID'
    else:
        rate = SETTINGS['rate_per_hour']
        subtotal = hours * rate
        disc_type = discount_override or sess.get('discount_type','none')
        disc_pct = 0
        if disc_type == 'senior': disc_pct = SETTINGS['discount_senior']
        elif disc_type == 'pwd': disc_pct = SETTINGS['discount_pwd']
        disc_amount = subtotal * disc_pct
        penalty = 0
        if hours > SETTINGS['max_stay_hours']:
            overstay_hours = hours - SETTINGS['max_stay_hours']
            penalty = overstay_hours * SETTINGS['penalty_per_hour']
            conn.execute("INSERT INTO violations (id,plate,slot_id,violation_type,detail,created) VALUES (?,?,?,?,?,?)",
                        (str(uuid.uuid4())[:8], sess['plate'], sess['slot_id'], 'OVERSTAY',
                         f'Overstayed by {overstay_hours:.1f}h', now.isoformat()))
        total = max(subtotal - disc_amount + penalty, SETTINGS['rate_per_hour'] * 0.5)
        payment_id = str(uuid.uuid4())[:12].upper()
        conn.execute("""INSERT INTO payments (id,session_id,plate,hours,rate,discount_pct,discount_type,
                        subtotal,discount_amount,penalty,total,created,username) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (payment_id, session_id, sess['plate'], round(hours,2), SETTINGS['rate_per_hour'],
                      disc_pct, disc_type, round(subtotal,2), round(disc_amount,2),
                      round(penalty,2), round(total,2), now.isoformat(), session['user']))
        conn.execute("INSERT INTO transactions (type,slot_id,plate,session_id,amount,username,created) VALUES (?,?,?,?,?,?,?)",
                     ('PARK_OUT', sess['slot_id'], sess['plate'], session_id, round(total,2), session['user'], now.isoformat()))

    conn.execute("UPDATE slots SET occupied=0, plate=NULL, session_id=NULL, entry_time=NULL WHERE slot_id=?", (sess['slot_id'],))
    conn.execute("UPDATE sessions SET exit=?,fee=?,status='closed' WHERE session_id=?",
                 (now.isoformat(), total, session_id))
    conn.commit()
    conn.close()

    transaction_stack.push({'type':'PARK_OUT','slot':sess['slot_id'],'plate':sess['plate'],'fee':total,'time':now.isoformat()})
    add_notification(session['user'], f'{sess["plate"]} exited. Fee: P{total:.2f}', 'success')

    if not waiting_queue.is_empty():
        next_up = waiting_queue.peek()
        add_notification(next_up['user'], f'Slot {sess["slot_id"]} is now available!', 'warning')

    socketio.emit('slot_update', {'slot_id': sess['slot_id'], 'occupied': False, 'plate': None})
    socketio.emit('analytics', get_analytics())

    return jsonify({
        'ok': True,
        'fee': round(total, 2),
        'hours': round(hours, 2),
        'plate': sess['plate'],
        'slot_id': sess['slot_id'],
        'payment_id': payment_id,
        'already_paid': bool(already_paid)
    })

# ── QR SCAN ───────────────────────────────────────────────────
@app.route('/api/qr/scan', methods=['POST'])
@login_required
def api_qr_scan():
    d = request.json
    qr_string = d.get('qr_data','').strip()

    # Handle reservation QR
    if qr_string.startswith('CYPARK-RES|'):
        parts = qr_string.split('|')
        if len(parts) >= 3:
            res_id = parts[1]
            conn = get_db()
            res = conn.execute("SELECT * FROM reservations WHERE id=?", (res_id,)).fetchone()
            conn.close()
            if res:
                res = dict(res)
                return jsonify({
                    'ok': True,
                    'type': 'reservation',
                    'reservation_id': res['id'],
                    'plate': res['plate'],
                    'slot_id': res['slot_id'],
                    'status': res['status'],
                    'expires_at': res['expires_at'],
                    'fee': res['fee']
                })
        return jsonify({'ok': False, 'msg': 'Invalid reservation QR'})

    parsed = parse_qr(qr_string)
    if not parsed:
        return jsonify({'ok': False, 'msg': 'Invalid QR code format'})

    conn = get_db()
    sess = conn.execute("SELECT * FROM sessions WHERE session_id=?", (parsed['session_id'],)).fetchone()
    if not sess:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Session not found'})

    sess = dict(sess)
    if sess['status'] != 'active':
        conn.execute("UPDATE sessions SET invalid_qr_attempts=invalid_qr_attempts+1 WHERE session_id=?", (parsed['session_id'],))
        conn.commit()
        conn.close()
        return jsonify({'ok': False, 'msg': 'QR Expired — session already closed', 'expired': True})

    entry = datetime.fromisoformat(sess['entry'])
    now = datetime.now()
    hours = max((now - entry).total_seconds() / 3600, 0.5)
    rate = SETTINGS['rate_per_hour']
    current_fee = round(hours * rate, 2)
    conn.close()

    return jsonify({
        'ok': True,
        'type': 'parking',
        'session_id': sess['session_id'],
        'plate': sess['plate'],
        'slot_id': sess['slot_id'],
        'entry': sess['entry'],
        'hours': round(hours, 2),
        'current_fee': current_fee,
        'discount_type': sess.get('discount_type','none'),
        'status': sess['status'],
        'paid_before_exit': bool(sess.get('paid_before_exit', 0))
    })

@app.route('/api/qr/lookup', methods=['POST'])
@login_required
def api_qr_lookup():
    d = request.json
    sid = d.get('session_id','').upper()
    conn = get_db()
    sess = conn.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
    conn.close()
    if not sess:
        return jsonify({'ok': False, 'msg': 'Session not found'})
    sess = dict(sess)
    entry = datetime.fromisoformat(sess['entry'])
    hours = max((datetime.now() - entry).total_seconds() / 3600, 0.5)
    return jsonify({'ok': True, **sess, 'hours': round(hours,2),
                    'current_fee': round(hours * SETTINGS['rate_per_hour'], 2)})

@app.route('/api/queue/join', methods=['POST'])
@login_required
def api_queue_join():
    d = request.json
    plate = d.get('plate','').upper()
    item = {'id': str(uuid.uuid4())[:8], 'user': session['user'], 'plate': plate,
            'time': datetime.now().isoformat()}
    waiting_queue.enqueue(item)
    conn = get_db()
    conn.execute("INSERT INTO queue (id,username,plate,joined_at) VALUES (?,?,?,?)",
                 (item['id'], session['user'], plate, item['time']))
    conn.commit()
    conn.close()
    add_notification(session['user'], f'{plate} in queue. Position: #{waiting_queue.size()}')
    return jsonify({'ok': True, 'position': waiting_queue.size(), 'queue': waiting_queue.to_list()})

@app.route('/api/queue')
def api_queue():
    return jsonify(waiting_queue.to_list())

@app.route('/api/queue/dequeue', methods=['POST'])
@login_required
def api_queue_dequeue():
    item = waiting_queue.dequeue()
    if item:
        add_notification(item['user'], 'Your turn! Please proceed to park.', 'success')
    return jsonify({'ok': True, 'item': item, 'queue': waiting_queue.to_list()})

@app.route('/api/session/live/<session_id>')
@login_required
def api_session_live(session_id):
    conn = get_db()
    sess = conn.execute("SELECT * FROM sessions WHERE session_id=? AND status='active'", (session_id,)).fetchone()
    conn.close()
    if not sess:
        return jsonify({'ok': False})
    sess = dict(sess)
    entry = datetime.fromisoformat(sess['entry'])
    now = datetime.now()
    elapsed = (now - entry).total_seconds()
    hours = max(elapsed / 3600, 0)
    return jsonify({
        'ok': True, 'elapsed_seconds': int(elapsed),
        'hours': round(hours, 4),
        'current_fee': round(max(hours, 0.5) * SETTINGS['rate_per_hour'], 2),
        'plate': sess['plate'], 'slot_id': sess['slot_id'],
        'paid_before_exit': bool(sess.get('paid_before_exit', 0))
    })

@app.route('/api/my_sessions')
@login_required
def api_my_sessions():
    conn = get_db()
    rows = conn.execute("SELECT * FROM sessions WHERE username=? ORDER BY entry DESC LIMIT 10", (session['user'],)).fetchall()
    conn.close()
    result = []
    for r in rows:
        s = dict(r)
        if s['status'] == 'active':
            entry = datetime.fromisoformat(s['entry'])
            s['elapsed_hours'] = round((datetime.now() - entry).total_seconds() / 3600, 2)
            s['current_fee'] = round(max(s['elapsed_hours'], 0.5) * SETTINGS['rate_per_hour'], 2)
        result.append(s)
    return jsonify(result)

@app.route('/api/violations')
@login_required
def api_violations():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM violations ORDER BY created DESC LIMIT 50").fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/violations/check', methods=['POST'])
@login_required
def api_check_violations():
    conn = get_db()
    active = conn.execute("SELECT * FROM sessions WHERE status='active'").fetchall()
    flagged = []
    now = datetime.now()
    for s in active:
        entry = datetime.fromisoformat(s['entry'])
        hours = (now - entry).total_seconds() / 3600
        if hours > SETTINGS['max_stay_hours']:
            flagged.append({'plate': s['plate'], 'slot_id': s['slot_id'], 'hours': round(hours,1), 'session_id': s['session_id']})
    conn.close()
    return jsonify({'violations': flagged})

@app.route('/api/violations/resolve', methods=['POST'])
@admin_required
def api_resolve_violation():
    vid = request.json.get('id')
    conn = get_db()
    conn.execute("UPDATE violations SET resolved=1 WHERE id=?", (vid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/invoice/<payment_id>')
@login_required
def api_invoice(payment_id):
    conn = get_db()
    p = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invoice not found'})
    p = dict(p)
    s = conn.execute("SELECT * FROM sessions WHERE session_id=?", (p['session_id'],)).fetchone()
    conn.close()
    p['session'] = dict(s) if s else {}
    p['invoice_number'] = f"CY-{payment_id}"
    p['issued_at'] = datetime.now().isoformat()
    return jsonify({'ok': True, 'invoice': p})

@app.route('/api/analytics')
@login_required
def api_analytics():
    period = request.args.get('period', 'week')
    return jsonify(get_analytics(period))

@app.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q','').strip()
    status = request.args.get('status','all')
    date = request.args.get('date','')
    conn = get_db()
    sql = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if q:
        sql += " AND (plate LIKE ? OR session_id LIKE ?)"
        params += [f'%{q}%', f'%{q}%']
    if status != 'all':
        sql += " AND status=?"
        params.append(status)
    if date:
        sql += " AND DATE(entry)=?"
        params.append(date)
    sql += " ORDER BY entry DESC LIMIT 100"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/notifications')
@login_required
def api_notifications():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM notifications WHERE username=? ORDER BY created DESC LIMIT 20", (session['user'],)).fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notif_read():
    nid = request.json.get('id')
    conn = get_db()
    conn.execute("UPDATE notifications SET read=1 WHERE id=? AND username=?", (nid, session['user']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/notifications/read_all', methods=['POST'])
@login_required
def api_notif_read_all():
    conn = get_db()
    conn.execute("UPDATE notifications SET read=1 WHERE username=?", (session['user'],))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/export/csv')
@admin_required
def api_export_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM sessions ORDER BY entry DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Session ID','Slot','Plate','User','Entry','Exit','Fee','Status'])
    for r in rows:
        writer.writerow([r['session_id'],r['slot_id'],r['plate'],r['username'],
                         r['entry'],r['exit'],r['fee'],r['status']])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name=f'cypark_sessions_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/admin/users')
@admin_required
def api_admin_users():
    conn = get_db()
    users = [dict(r) for r in conn.execute("SELECT username,name,email,role,created,blocked,last_login FROM users").fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/api/admin/block', methods=['POST'])
@admin_required
def api_admin_block():
    d = request.json
    u = d.get('username')
    if u == 'admin': return jsonify({'ok': False, 'msg': "Can't block admin"})
    conn = get_db()
    conn.execute("UPDATE users SET blocked=? WHERE username=?", (1 if d.get('block',True) else 0, u))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/admin/override', methods=['POST'])
@admin_required
def api_admin_override():
    slot_id = request.json.get('slot_id')
    conn = get_db()
    sess = conn.execute("SELECT session_id FROM sessions WHERE slot_id=? AND status='active'", (slot_id,)).fetchone()
    if sess:
        conn.execute("UPDATE sessions SET status='admin_override',exit=? WHERE session_id=?",
                     (datetime.now().isoformat(), sess['session_id']))
    conn.execute("UPDATE slots SET occupied=0, plate=NULL, session_id=NULL, entry_time=NULL WHERE slot_id=?", (slot_id,))
    conn.commit()
    conn.close()
    socketio.emit('slot_update', {'slot_id': slot_id, 'occupied': False, 'plate': None})
    return jsonify({'ok': True})

@app.route('/api/admin/stats')
@admin_required
def api_admin_stats():
    analytics = get_analytics()
    conn = get_db()
    analytics['total_users'] = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    analytics['total_violations'] = conn.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
    conn.close()
    return jsonify(analytics)

@app.route('/api/admin/sessions')
@admin_required
def api_admin_sessions():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY entry DESC LIMIT 100").fetchall()]
    conn.close()
    for s in rows:
        if s['status'] == 'active':
            entry = datetime.fromisoformat(s['entry'])
            s['elapsed_hours'] = round((datetime.now() - entry).total_seconds() / 3600, 2)
    return jsonify(rows)

@app.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    return jsonify(SETTINGS)

@app.route('/api/settings', methods=['POST'])
@admin_required
def api_save_settings():
    d = request.json
    allowed = ['rate_per_hour','penalty_per_hour','max_stay_hours','discount_senior',
               'discount_pwd','emergency_mode','grace_period_minutes','brute_force_limit','reservation_fee']
    for k in allowed:
        if k in d:
            if k == 'emergency_mode':
                SETTINGS[k] = bool(d[k])
            else:
                try: SETTINGS[k] = float(d[k]) if '.' in str(d[k]) else int(d[k])
                except: pass
    save_settings()
    if SETTINGS.get('emergency_mode'):
        socketio.emit('emergency', {'active': True, 'msg': 'Emergency Mode Activated'})
    else:
        socketio.emit('emergency', {'active': False})
    return jsonify({'ok': True, 'settings': SETTINGS})

@app.route('/api/emergency/toggle', methods=['POST'])
@admin_required
def api_emergency_toggle():
    SETTINGS['emergency_mode'] = not SETTINGS['emergency_mode']
    save_settings()
    socketio.emit('emergency', {'active': SETTINGS['emergency_mode'],
                                'msg': 'Emergency Mode Activated' if SETTINGS['emergency_mode'] else 'System Restored'})
    return jsonify({'ok': True, 'emergency': SETTINGS['emergency_mode']})

@app.route('/api/transactions')
@login_required
def api_transactions():
    return jsonify(transaction_stack.to_list()[:30])

@socketio.on('connect')
def on_connect():
    emit('connected', {'msg': 'CYPARK connected'})
    emit('analytics', get_analytics())
    emit('emergency', {'active': SETTINGS.get('emergency_mode', False)})

@socketio.on('request_analytics')
def on_analytics():
    emit('analytics', get_analytics())

if __name__ == '__main__':
    print("CYPARK starting on http://localhost:5000")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
