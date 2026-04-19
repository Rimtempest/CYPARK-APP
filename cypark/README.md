# CYPARK — Smart Parking Management System

## Quick Start

```bash
pip install -r requirements.txt
python run.py
```
Open: http://localhost:5000

## Default Admin Login
- Username: `admin`
- Password: `admin123`

## Features

### Fixed Issues
- Admin password bug fixed (was wrong hash in DB)
- QR code now generates correctly with white QR on dark background
- Ticket shown as phone screen view with QR code
- No emojis anywhere in the UI

### New Features
- **Splash screen** with car logo animation on startup
- **Loading animation** after login before dashboard
- **Professional UI** — no emojis, clean brand design
- **Sound effects** — click sounds, success chime, error buzz, startup melody
- **Slot Reservation** — reserve a slot for 30 mins (P50 fee)
- **Pay Before Exit** — pay while still parked, exit anytime
- **Phone-style ticket** — QR code shown in a phone frame
- **Camera QR scanner** — admin can scan customer QR via camera
- **GPS Map** — real SM Fairview location in Google Maps embed
- **SM Fairview floor plan** — visual parking grid matching real layout
- **Waze + Google Maps** links for navigation

## Parking Layout
- Floor A: Ground Level (North + South wings, 5 slots each)
- Floor B: Level 2 (North + South wings, 5 slots each)
- Floor C: Rooftop (North + South wings, 5 slots each)
- Total: 30 slots

## Rates
- Hourly: P40/hr
- Reservation fee: P50 (30-min hold)
- Overstay penalty: P20/hr
- Senior/PWD discount: 20%
