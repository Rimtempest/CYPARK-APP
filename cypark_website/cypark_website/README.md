# CYPARK — Smart Parking System (Static Web Version)

A fully static, client-side parking management system — no server required. All data is stored in your browser's `localStorage`.

## 🚀 Hosting on GitHub Pages

1. Create a new repository on GitHub (e.g. `cypark`)
2. Upload all files in this folder to the repository root
3. Go to **Settings → Pages → Source → Deploy from branch → main → / (root)**
4. Your site will be live at `https://yourusername.github.io/cypark/`

## 📁 File Structure

```
index.html        — Login page
dashboard.html    — Main app dashboard
js/
  store.js        — localStorage data layer
  qr.js           — QR code generation helpers
  api.js          — All business logic (replaces Flask backend)
```

## 🔑 Default Login

- **Username:** `admin`
- **Password:** `admin123`

## 💾 Data Storage

All data is stored in your browser's **localStorage** under the `cypark_` prefix. Data persists between sessions on the same browser/device.

> ⚠️ Note: localStorage is per-browser. Different browsers or devices will not share data. For multi-device data sharing, you would need a backend server.

## ✅ Features

- Login / Register
- Park vehicle (auto-assign or manual slot)
- QR code ticket generation (visible on screen)
- Reserve slots (30 min hold)
- Process exit + fee calculation
- Pay before exit
- QR scanner (camera or manual entry)
- SM Fairview floor plan map
- Sessions log with search
- Admin panel (settings, user management, override)
- Revenue charts & occupancy analytics
- Notifications system
- Emergency mode toggle
- Senior / PWD discounts
- Overstay penalty calculation
