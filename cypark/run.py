"""
CYPARK - Smart Parking Management System
Run this file to start the server.

Default admin credentials:
  Username: admin
  Password: admin123

Access: http://localhost:5000
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app, socketio

if __name__ == '__main__':
    print("=" * 50)
    print("  CYPARK Smart Parking System")
    print("=" * 50)
    print("  URL:      http://localhost:5000")
    print("  Admin:    admin / admin123")
    print("=" * 50)
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
