// CYPARK API — Client-side implementation of all Flask routes
// Replaces the Python backend with pure JS logic

const API = (() => {
  // ── AUTH ─────────────────────────────────────────────────
  async function login(username, password) {
    const users = Store.getUsers();
    const u = username.trim().toLowerCase();
    const user = users.find(x => x.username === u);
    if (!user) return { ok: false, msg: 'Invalid credentials' };
    if (user.blocked) return { ok: false, msg: 'Account suspended. Contact administrator.' };
    const settings = Store.getSettings();
    const limit = settings.brute_force_limit || 5;
    if ((user.failed_logins || 0) >= limit) return { ok: false, msg: `Account locked after ${limit} failed attempts.` };
    const hashed = await Store.hashPw(password);
    if (user.password !== hashed) {
      user.failed_logins = (user.failed_logins || 0) + 1;
      Store.saveUsers(users);
      const remaining = limit - user.failed_logins;
      return { ok: false, msg: `Invalid credentials. ${remaining} attempts remaining.` };
    }
    user.failed_logins = 0;
    user.last_login = new Date().toISOString();
    Store.saveUsers(users);
    return { ok: true, role: user.role, name: user.name, username: u };
  }

  async function register(data) {
    if (!data.username || !data.password || !data.name) return { ok: false, msg: 'All fields required' };
    const users = Store.getUsers();
    const u = data.username.trim().toLowerCase();
    if (users.find(x => x.username === u)) return { ok: false, msg: 'Username already taken' };
    const hashed = await Store.hashPw(data.password);
    users.push({ username: u, password: hashed, name: data.name, email: data.email || '', role: 'user', created: new Date().toISOString(), blocked: false, failed_logins: 0, last_login: null });
    Store.saveUsers(users);
    Store.addNotification(u, `Welcome to CYPARK, ${data.name}! Your account is ready.`, 'success');
    return { ok: true };
  }

  // ── SLOTS ─────────────────────────────────────────────────
  function getSlots() { return Store.getSlots(); }

  function recommend(preference = 'nearest_exit') {
    const rec = Store.recommendSlot(preference);
    const slots = Store.getSlots();
    const stats = {};
    ['A', 'B', 'C'].forEach(f => {
      const total = slots.filter(s => s.floor === f).length;
      const occ = slots.filter(s => s.floor === f && s.occupied).length;
      stats[f] = { total, occupied: occ, vacant: total - occ };
    });
    return { slot: rec.slot, path: rec.path, floor_stats: stats };
  }

  // ── PARK ──────────────────────────────────────────────────
  function park(sessionUser, { plate, slot_id, discount_type = 'none' }) {
    const settings = Store.getSettings();
    if (settings.emergency_mode) return { ok: false, msg: 'EMERGENCY MODE ACTIVE — Entry disabled' };
    plate = (plate || '').toUpperCase().trim();
    if (!plate) return { ok: false, msg: 'Plate number required' };

    const sessions = Store.getSessions();
    if (sessions.find(s => s.plate === plate && s.status === 'active')) return { ok: false, msg: `Plate ${plate} already has an active session` };

    let slots = Store.getSlots();
    let sid = slot_id ? slot_id.toUpperCase().trim() : null;

    // Check for reservation
    const reservations = Store.getReservations();
    const res = reservations.find(r => r.username === sessionUser.username && r.plate === plate && r.status === 'active');
    if (res) {
      sid = res.slot_id;
      res.status = 'used';
      Store.saveReservations(reservations);
    }

    if (sid) {
      const slot = slots.find(s => s.slot_id === sid && !s.occupied);
      if (!slot) return { ok: false, msg: 'Slot unavailable or already occupied' };
    } else {
      const rec = Store.recommendSlot();
      if (!rec.slot) return { ok: false, msg: 'No vacant slots available' };
      sid = rec.slot;
    }

    const session_id = Store.genId(12);
    const now = new Date().toISOString();
    const qr_data = makeQRData(session_id, sid, plate, now);
    const qr = generateQR(qr_data);

    const slot = slots.find(s => s.slot_id === sid);
    slot.occupied = true; slot.plate = plate; slot.session_id = session_id; slot.entry_time = now;
    slot.reserved = false; slot.reserved_by = null; slot.reserved_until = null;
    Store.saveSlots(slots);

    sessions.push({ session_id, slot_id: sid, plate, username: sessionUser.username, entry: now, exit: null, fee: null, discount_type, status: 'active', qr_data, invalid_qr_attempts: 0, paid_before_exit: false });
    Store.saveSessions(sessions);

    const txns = Store.getTransactions();
    txns.unshift({ id: Store.shortId(), type: 'PARK_IN', slot_id: sid, plate, session_id, amount: 0, username: sessionUser.username, created: now });
    Store.saveTransactions(txns);

    Store.addNotification(sessionUser.username, `${plate} parked at ${sid}`, 'success');
    window.dispatchEvent(new CustomEvent('cypark:slot_update'));
    window.dispatchEvent(new CustomEvent('cypark:analytics'));

    return { ok: true, session_id, slot_id: sid, qr, entry: now, qr_data, plate };
  }

  // ── RESERVE ───────────────────────────────────────────────
  function reserve(sessionUser, { plate, slot_id }) {
    plate = (plate || '').toUpperCase().trim();
    if (!plate) return { ok: false, msg: 'Plate number required' };
    const reservations = Store.getReservations();
    if (reservations.find(r => r.username === sessionUser.username && r.status === 'active')) return { ok: false, msg: 'You already have an active reservation' };

    let slots = Store.getSlots();
    let sid = slot_id ? slot_id.toUpperCase().trim() : null;
    if (sid) {
      const slot = slots.find(s => s.slot_id === sid && !s.occupied && !s.reserved);
      if (!slot) return { ok: false, msg: 'Slot unavailable or already reserved' };
    } else {
      const rec = Store.recommendSlot();
      if (!rec.slot) return { ok: false, msg: 'No vacant slots available' };
      sid = rec.slot;
    }

    const settings = Store.getSettings();
    const fee = settings.reservation_fee || 50;
    const res_id = Store.genId(12);
    const now = new Date();
    const expires = new Date(now.getTime() + 30 * 60000);

    const slot = slots.find(s => s.slot_id === sid);
    slot.reserved = true; slot.reserved_by = sessionUser.username; slot.reserved_until = expires.toISOString();
    Store.saveSlots(slots);

    reservations.push({ id: res_id, username: sessionUser.username, plate, slot_id: sid, reserved_at: now.toISOString(), expires_at: expires.toISOString(), status: 'active', fee });
    Store.saveReservations(reservations);

    const qr_data = `CYPARK-RES|${res_id}|${sid}|${plate}|${now.toISOString()}`;
    const qr = generateQR(qr_data);

    Store.addNotification(sessionUser.username, `Slot ${sid} reserved for ${plate}. Valid 30 minutes. Fee: P${fee}`, 'success');
    window.dispatchEvent(new CustomEvent('cypark:slot_update'));

    return { ok: true, reservation_id: res_id, slot_id: sid, plate, expires_at: expires.toISOString(), fee, qr, qr_data };
  }

  function cancelReservation(sessionUser, reservation_id) {
    const reservations = Store.getReservations();
    const res = reservations.find(r => r.id === reservation_id.toUpperCase() && r.username === sessionUser.username && r.status === 'active');
    if (!res) return { ok: false, msg: 'Reservation not found' };
    res.status = 'cancelled';
    Store.saveReservations(reservations);
    const slots = Store.getSlots();
    const slot = slots.find(s => s.slot_id === res.slot_id);
    if (slot) { slot.reserved = false; slot.reserved_by = null; slot.reserved_until = null; }
    Store.saveSlots(slots);
    window.dispatchEvent(new CustomEvent('cypark:slot_update'));
    return { ok: true };
  }

  function myReservations(sessionUser) {
    return Store.getReservations().filter(r => r.username === sessionUser.username).sort((a, b) => b.reserved_at.localeCompare(a.reserved_at)).slice(0, 10);
  }

  // ── CALCULATIONS ──────────────────────────────────────────
  function calcFee(sess, discountOverride) {
    const settings = Store.getSettings();
    const entry = new Date(sess.entry);
    const now = new Date();
    const hours = Math.max((now - entry) / 3600000, 0.5);
    const rate = settings.rate_per_hour;
    const subtotal = hours * rate;
    const disc_type = discountOverride || sess.discount_type || 'none';
    let disc_pct = 0;
    if (disc_type === 'senior') disc_pct = settings.discount_senior || 0.20;
    else if (disc_type === 'pwd') disc_pct = settings.discount_pwd || 0.20;
    const disc_amount = subtotal * disc_pct;
    let penalty = 0;
    if (hours > settings.max_stay_hours) {
      penalty = (hours - settings.max_stay_hours) * settings.penalty_per_hour;
    }
    const total = Math.max(subtotal - disc_amount + penalty, rate * 0.5);
    return { hours: Math.round(hours * 100) / 100, rate, subtotal: Math.round(subtotal * 100) / 100, disc_type, disc_pct, disc_amount: Math.round(disc_amount * 100) / 100, penalty: Math.round(penalty * 100) / 100, total: Math.round(total * 100) / 100 };
  }

  // ── PAY BEFORE EXIT ───────────────────────────────────────
  function payBeforeExit(sessionUser, { session_id, discount_type }) {
    session_id = (session_id || '').toUpperCase().trim();
    const sessions = Store.getSessions();
    const sess = sessions.find(s => s.session_id === session_id && s.status === 'active');
    if (!sess) return { ok: false, msg: 'Invalid or already closed session' };
    if (sess.paid_before_exit) return { ok: false, msg: 'Already paid. Please proceed to exit.' };

    const calc = calcFee(sess, discount_type);
    const payment_id = Store.genId(12);
    const now = new Date().toISOString();

    sess.paid_before_exit = true;
    if (discount_type) sess.discount_type = discount_type;
    Store.saveSessions(sessions);

    const payments = Store.getPayments();
    payments.push({ id: payment_id, session_id, plate: sess.plate, hours: calc.hours, rate: calc.rate, discount_pct: calc.disc_pct, discount_type: calc.disc_type, subtotal: calc.subtotal, discount_amount: calc.disc_amount, penalty: calc.penalty, total: calc.total, created: now, username: sessionUser.username, paid_before_exit: true });
    Store.savePayments(payments);

    Store.addNotification(sessionUser.username, `Payment confirmed for ${sess.plate}. P${calc.total.toFixed(2)}. You may exit anytime.`, 'success');
    return { ok: true, payment_id, fee: calc.total, hours: calc.hours, plate: sess.plate, slot_id: sess.slot_id, subtotal: calc.subtotal, discount_amount: calc.disc_amount, discount_type: calc.disc_type, penalty: calc.penalty, rate: calc.rate };
  }

  // ── EXIT ──────────────────────────────────────────────────
  function processExit(sessionUser, { session_id, discount_type }) {
    session_id = (session_id || '').toUpperCase().trim();
    const sessions = Store.getSessions();
    const sess = sessions.find(s => s.session_id === session_id && s.status === 'active');
    if (!sess) return { ok: false, msg: 'Invalid or already closed session' };

    const now = new Date().toISOString();
    let total, payment_id;

    if (sess.paid_before_exit) {
      const payments = Store.getPayments();
      const p = payments.find(px => px.session_id === session_id && px.paid_before_exit);
      total = p ? p.total : 0;
      payment_id = p ? p.id : 'ALREADY_PAID';
    } else {
      const calc = calcFee(sess, discount_type);
      total = calc.total;
      payment_id = Store.genId(12);
      const payments = Store.getPayments();
      payments.push({ id: payment_id, session_id, plate: sess.plate, hours: calc.hours, rate: calc.rate, discount_pct: calc.disc_pct, discount_type: calc.disc_type, subtotal: calc.subtotal, discount_amount: calc.disc_amount, penalty: calc.penalty, total: calc.total, created: now, username: sessionUser.username, paid_before_exit: false });
      Store.savePayments(payments);

      if (calc.penalty > 0) {
        const violations = Store.getViolations();
        violations.push({ id: Store.shortId(), plate: sess.plate, slot_id: sess.slot_id, violation_type: 'OVERSTAY', detail: `Overstayed by ${(calc.hours - Store.getSettings().max_stay_hours).toFixed(1)}h`, created: now, resolved: false });
        Store.saveViolations(violations);
      }

      const txns = Store.getTransactions();
      txns.unshift({ id: Store.shortId(), type: 'PARK_OUT', slot_id: sess.slot_id, plate: sess.plate, session_id, amount: calc.total, username: sessionUser.username, created: now });
      Store.saveTransactions(txns);
    }

    const slots = Store.getSlots();
    const slot = slots.find(s => s.slot_id === sess.slot_id);
    if (slot) { slot.occupied = false; slot.plate = null; slot.session_id = null; slot.entry_time = null; }
    Store.saveSlots(slots);

    sess.exit = now; sess.fee = total; sess.status = 'closed';
    Store.saveSessions(sessions);

    Store.addNotification(sessionUser.username, `${sess.plate} exited. Fee: P${total.toFixed(2)}`, 'success');
    window.dispatchEvent(new CustomEvent('cypark:slot_update'));
    window.dispatchEvent(new CustomEvent('cypark:analytics'));

    const calc2 = calcFee(sess, discount_type);
    return { ok: true, fee: total, hours: calc2.hours, plate: sess.plate, slot_id: sess.slot_id, payment_id, already_paid: sess.paid_before_exit, rate: calc2.rate, discount_amount: calc2.disc_amount, discount_type: calc2.disc_type, penalty: calc2.penalty };
  }

  // ── QR SCAN ───────────────────────────────────────────────
  function qrScan(qr_string) {
    if (qr_string.startsWith('CYPARK-RES|')) {
      const parts = qr_string.split('|');
      if (parts.length >= 3) {
        const res_id = parts[1];
        const res = Store.getReservations().find(r => r.id === res_id);
        if (res) return { ok: true, type: 'reservation', reservation_id: res.id, plate: res.plate, slot_id: res.slot_id, status: res.status, expires_at: res.expires_at, fee: res.fee };
      }
      return { ok: false, msg: 'Invalid reservation QR' };
    }
    const parsed = parseQR(qr_string);
    if (!parsed) return { ok: false, msg: 'Invalid QR code format' };
    const sess = Store.getSessions().find(s => s.session_id === parsed.session_id);
    if (!sess) return { ok: false, msg: 'Session not found' };
    if (sess.status !== 'active') return { ok: false, msg: 'QR Expired — session already closed', expired: true };
    const entry = new Date(sess.entry);
    const hours = Math.max((new Date() - entry) / 3600000, 0.5);
    const settings = Store.getSettings();
    return { ok: true, type: 'parking', session_id: sess.session_id, plate: sess.plate, slot_id: sess.slot_id, entry: sess.entry, hours: Math.round(hours * 100) / 100, current_fee: Math.round(hours * settings.rate_per_hour * 100) / 100, discount_type: sess.discount_type || 'none', status: sess.status, paid_before_exit: !!sess.paid_before_exit };
  }

  function qrLookup(session_id) {
    session_id = (session_id || '').toUpperCase();
    const sess = Store.getSessions().find(s => s.session_id === session_id);
    if (!sess) return { ok: false, msg: 'Session not found' };
    const hours = Math.max((new Date() - new Date(sess.entry)) / 3600000, 0);
    const settings = Store.getSettings();
    return { ok: true, ...sess, hours: Math.round(hours * 100) / 100, current_fee: Math.round(Math.max(hours, 0.5) * settings.rate_per_hour * 100) / 100 };
  }

  // ── SESSIONS ──────────────────────────────────────────────
  function mySessions(sessionUser) {
    const sessions = Store.getSessions().filter(s => s.username === sessionUser.username).sort((a, b) => b.entry.localeCompare(a.entry)).slice(0, 10);
    const settings = Store.getSettings();
    return sessions.map(s => {
      if (s.status === 'active') {
        const hours = Math.max((new Date() - new Date(s.entry)) / 3600000, 0);
        return { ...s, elapsed_hours: Math.round(hours * 100) / 100, current_fee: Math.round(Math.max(hours, 0.5) * settings.rate_per_hour * 100) / 100 };
      }
      return s;
    });
  }

  function sessionLive(session_id) {
    const sess = Store.getSessions().find(s => s.session_id === session_id && s.status === 'active');
    if (!sess) return { ok: false };
    const settings = Store.getSettings();
    const elapsed = (new Date() - new Date(sess.entry)) / 1000;
    const hours = elapsed / 3600;
    return { ok: true, elapsed_seconds: Math.floor(elapsed), hours: Math.round(hours * 10000) / 10000, current_fee: Math.round(Math.max(hours, 0.5) * settings.rate_per_hour * 100) / 100, plate: sess.plate, slot_id: sess.slot_id, paid_before_exit: !!sess.paid_before_exit };
  }

  function searchSessions({ q, status, date }) {
    let sessions = Store.getSessions();
    if (q) { const qq = q.toUpperCase(); sessions = sessions.filter(s => (s.plate || '').includes(qq) || (s.session_id || '').includes(qq)); }
    if (status && status !== 'all') sessions = sessions.filter(s => s.status === status);
    if (date) sessions = sessions.filter(s => s.entry && s.entry.startsWith(date));
    return sessions.sort((a, b) => b.entry.localeCompare(a.entry)).slice(0, 100);
  }

  // ── NOTIFICATIONS ─────────────────────────────────────────
  function getNotifications(sessionUser) {
    return Store.getNotifications().filter(n => n.username === sessionUser.username).slice(0, 20);
  }

  function readNotification(sessionUser, id) {
    const notifs = Store.getNotifications();
    const n = notifs.find(x => x.id === id && x.username === sessionUser.username);
    if (n) { n.read = true; Store.saveNotifications(notifs); }
    return { ok: true };
  }

  function readAllNotifications(sessionUser) {
    const notifs = Store.getNotifications();
    notifs.filter(n => n.username === sessionUser.username).forEach(n => n.read = true);
    Store.saveNotifications(notifs);
    return { ok: true };
  }

  // ── QUEUE ─────────────────────────────────────────────────
  function joinQueue(sessionUser, plate) {
    const queue = Store.getQueue();
    const item = { id: Store.shortId(), user: sessionUser.username, plate, time: new Date().toISOString(), status: 'waiting' };
    queue.push(item);
    Store.saveQueue(queue);
    const pos = queue.filter(q => q.status === 'waiting').length;
    Store.addNotification(sessionUser.username, `${plate} in queue. Position: #${pos}`);
    return { ok: true, position: pos, queue: queue.filter(q => q.status === 'waiting') };
  }

  function getQueueList() { return Store.getQueue().filter(q => q.status === 'waiting'); }

  // ── ADMIN ─────────────────────────────────────────────────
  function adminUsers() { return Store.getUsers().map(u => ({ username: u.username, name: u.name, email: u.email, role: u.role, created: u.created, blocked: u.blocked, last_login: u.last_login })); }

  function adminBlock(username, block) {
    if (username === 'admin') return { ok: false, msg: "Can't block admin" };
    const users = Store.getUsers();
    const u = users.find(x => x.username === username);
    if (!u) return { ok: false, msg: 'User not found' };
    u.blocked = block;
    Store.saveUsers(users);
    return { ok: true };
  }

  function adminOverride(slot_id) {
    const sessions = Store.getSessions();
    const sess = sessions.find(s => s.slot_id === slot_id && s.status === 'active');
    if (sess) { sess.status = 'admin_override'; sess.exit = new Date().toISOString(); }
    Store.saveSessions(sessions);
    const slots = Store.getSlots();
    const slot = slots.find(s => s.slot_id === slot_id);
    if (slot) { slot.occupied = false; slot.plate = null; slot.session_id = null; slot.entry_time = null; }
    Store.saveSlots(slots);
    window.dispatchEvent(new CustomEvent('cypark:slot_update'));
    return { ok: true };
  }

  function adminSessions() {
    return Store.getSessions().sort((a, b) => b.entry.localeCompare(a.entry)).slice(0, 100).map(s => {
      if (s.status === 'active') {
        const hours = Math.max((new Date() - new Date(s.entry)) / 3600000, 0);
        return { ...s, elapsed_hours: Math.round(hours * 100) / 100 };
      }
      return s;
    });
  }

  function getSettings() { return Store.getSettings(); }

  function saveSettings(data) {
    const settings = Store.getSettings();
    const allowed = ['rate_per_hour', 'penalty_per_hour', 'max_stay_hours', 'discount_senior', 'discount_pwd', 'emergency_mode', 'grace_period_minutes', 'brute_force_limit', 'reservation_fee'];
    allowed.forEach(k => { if (k in data) settings[k] = k === 'emergency_mode' ? !!data[k] : parseFloat(data[k]) || data[k]; });
    Store.saveSettings(settings);
    if (settings.emergency_mode) window.dispatchEvent(new CustomEvent('cypark:emergency', { detail: { active: true } }));
    else window.dispatchEvent(new CustomEvent('cypark:emergency', { detail: { active: false } }));
    return { ok: true, settings };
  }

  function toggleEmergency() {
    const settings = Store.getSettings();
    settings.emergency_mode = !settings.emergency_mode;
    Store.saveSettings(settings);
    window.dispatchEvent(new CustomEvent('cypark:emergency', { detail: { active: settings.emergency_mode } }));
    return { ok: true, emergency: settings.emergency_mode };
  }

  function getAdminStats() {
    const analytics = Store.getAnalytics();
    analytics.total_users = Store.getUsers().length;
    analytics.total_violations = Store.getViolations().length;
    return analytics;
  }

  function getTransactions() { return Store.getTransactions().slice(0, 30); }
  function getAnalytics(period = 'week') { return Store.getAnalytics(period); }

  return {
    login, register, getSlots, recommend, park, reserve, cancelReservation, myReservations,
    payBeforeExit, processExit, qrScan, qrLookup, mySessions, sessionLive, searchSessions,
    getNotifications, readNotification, readAllNotifications, joinQueue, getQueueList,
    adminUsers, adminBlock, adminOverride, adminSessions, getSettings, saveSettings,
    toggleEmergency, getAdminStats, getTransactions, getAnalytics
  };
})();
