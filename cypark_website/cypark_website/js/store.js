// CYPARK — In-browser data store (replaces SQLite backend)
// All data persisted in localStorage with JSON serialization

const Store = (() => {
  const PREFIX = 'cypark_';

  function get(key) {
    try { return JSON.parse(localStorage.getItem(PREFIX + key) || 'null'); } catch { return null; }
  }
  function set(key, val) {
    try { localStorage.setItem(PREFIX + key, JSON.stringify(val)); return true; } catch { return false; }
  }

  // ── DEFAULTS ──────────────────────────────────────────────
  function initDB() {
    if (get('initialized')) return;

    // Admin user (password: admin123 → SHA256)
    const users = [{
      username: 'admin',
      password: '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
      name: 'Administrator',
      email: 'admin@cypark.ph',
      role: 'admin',
      created: new Date().toISOString(),
      blocked: false,
      failed_logins: 0,
      last_login: null
    }];
    set('users', users);

    // Parking slots — SM Fairview layout
    const slots = [];
    ['A', 'B', 'C'].forEach(floor => {
      ['North', 'South'].forEach(zone => {
        for (let i = 1; i <= 5; i++) {
          const sid = `${floor}${zone[0]}-${String(i).padStart(2, '0')}`;
          slots.push({ slot_id: sid, floor, zone, occupied: false, plate: null, session_id: null, entry_time: null, slot_type: 'regular', reserved: false, reserved_by: null, reserved_until: null });
        }
      });
    });
    set('slots', slots);
    set('sessions', []);
    set('reservations', []);
    set('transactions', []);
    set('payments', []);
    set('violations', []);
    set('notifications', []);
    set('queue', []);
    set('settings', {
      rate_per_hour: 40,
      penalty_per_hour: 20,
      max_stay_hours: 24,
      slot_count: 30,
      discount_senior: 0.20,
      discount_pwd: 0.20,
      emergency_mode: false,
      grace_period_minutes: 15,
      brute_force_limit: 5,
      reservation_fee: 50
    });
    set('initialized', true);
  }

  // ── GETTERS ───────────────────────────────────────────────
  function getUsers() { return get('users') || []; }
  function getSlots() { return get('slots') || []; }
  function getSessions() { return get('sessions') || []; }
  function getReservations() { return get('reservations') || []; }
  function getTransactions() { return get('transactions') || []; }
  function getPayments() { return get('payments') || []; }
  function getViolations() { return get('violations') || []; }
  function getNotifications() { return get('notifications') || []; }
  function getQueue() { return get('queue') || []; }
  function getSettings() { return get('settings') || {}; }

  // ── SETTERS ───────────────────────────────────────────────
  function saveUsers(d) { set('users', d); }
  function saveSlots(d) { set('slots', d); }
  function saveSessions(d) { set('sessions', d); }
  function saveReservations(d) { set('reservations', d); }
  function saveTransactions(d) { set('transactions', d); }
  function savePayments(d) { set('payments', d); }
  function saveViolations(d) { set('violations', d); }
  function saveNotifications(d) { set('notifications', d); }
  function saveQueue(d) { set('queue', d); }
  function saveSettings(d) { set('settings', d); }

  // ── HELPERS ───────────────────────────────────────────────
  function genId(len = 12) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let r = '';
    for (let i = 0; i < len; i++) r += chars[Math.floor(Math.random() * chars.length)];
    return r;
  }

  function shortId(len = 8) { return genId(len); }

  async function hashPw(pw) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(pw));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  function addNotification(username, message, type = 'info') {
    const notifs = getNotifications();
    notifs.unshift({ id: shortId(), username, message, type, created: new Date().toISOString(), read: false });
    if (notifs.length > 100) notifs.splice(100);
    saveNotifications(notifs);
    window.dispatchEvent(new CustomEvent('cypark:notification', { detail: { username, message, type } }));
  }

  function recommendSlot(preference = 'nearest_exit') {
    const slots = getSlots();
    const vacant = slots.filter(s => !s.occupied && !s.reserved);
    if (!vacant.length) return { slot: null, path: null };
    const floorOrder = { A: 0, B: 1, C: 2 };
    if (preference === 'nearest_exit') {
      vacant.sort((a, b) => floorOrder[a.floor] - floorOrder[b.floor] || a.slot_id.localeCompare(b.slot_id));
    } else {
      const counts = {};
      ['A', 'B', 'C'].forEach(f => { counts[f] = slots.filter(s => s.floor === f && s.occupied).length; });
      vacant.sort((a, b) => counts[a.floor] - counts[b.floor] || a.slot_id.localeCompare(b.slot_id));
    }
    const best = vacant[0];
    const paths = { A: 'Ground Level — Enter main gate, proceed straight', B: 'Level 2 — Take ramp at entrance, turn right', C: 'Rooftop — Take ramp to top level, open parking' };
    return { slot: best.slot_id, path: paths[best.floor] || 'Follow directional signs' };
  }

  function getAnalytics(period = 'week') {
    const slots = getSlots();
    const sessions = getSessions();
    const payments = getPayments();
    const now = new Date();
    const total = slots.length;
    const occupied = slots.filter(s => s.occupied).length;
    const today = now.toISOString().slice(0, 10);
    const revToday = payments.filter(p => p.created && p.created.startsWith(today)).reduce((s, p) => s + (p.total || 0), 0);

    let revenue_chart = [];
    const days = period === 'month' ? 30 : 7;
    for (let i = 0; i < days; i++) {
      const d = new Date(now); d.setDate(d.getDate() - (days - 1 - i));
      const ds = d.toISOString().slice(0, 10);
      const amt = payments.filter(p => p.created && p.created.startsWith(ds)).reduce((s, p) => s + (p.total || 0), 0);
      revenue_chart.push({ label: period === 'month' ? d.getDate().toString() : d.toLocaleDateString('en-US', { weekday: 'short' }), amount: Math.round(amt * 100) / 100 });
    }

    const closedSessions = sessions.filter(s => s.status === 'closed' && s.exit && s.entry);
    const avgDur = closedSessions.length ? closedSessions.reduce((s, sess) => s + (new Date(sess.exit) - new Date(sess.entry)) / 3600000, 0) / closedSessions.length : 0;
    const totalRev = payments.reduce((s, p) => s + (p.total || 0), 0);

    return {
      total, occupied, vacant: total - occupied,
      occupancy_rate: total ? Math.round(occupied / total * 1000) / 10 : 0,
      revenue_today: Math.round(revToday * 100) / 100,
      revenue_chart,
      total_revenue: Math.round(totalRev * 100) / 100,
      total_sessions: sessions.length,
      active_sessions: sessions.filter(s => s.status === 'active').length,
      avg_duration: Math.round(avgDur * 100) / 100,
      violations: getViolations().filter(v => !v.resolved).length,
      queue_size: getQueue().filter(q => q.status === 'waiting').length
    };
  }

  initDB();

  return {
    getUsers, getSlots, getSessions, getReservations, getTransactions, getPayments,
    getViolations, getNotifications, getQueue, getSettings,
    saveUsers, saveSlots, saveSessions, saveReservations, saveTransactions,
    savePayments, saveViolations, saveNotifications, saveQueue, saveSettings,
    addNotification, recommendSlot, getAnalytics, genId, shortId, hashPw
  };
})();
