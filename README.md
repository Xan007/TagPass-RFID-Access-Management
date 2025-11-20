# TagPass - RFID Access Management System

## Overview

TagPass is a comprehensive access control system that combines cloud-based administration with edge computing for real-time RFID card reader management. The system enables institutions to control physical access to spaces through RFID cards, with centralized administration through a web dashboard and distributed offline capabilities through Raspberry Pi devices.

The architecture follows a hybrid cloud-local model: Supabase PostgreSQL serves as the authoritative data source and administrative interface, while Raspberry Pi devices maintain local SQLite caches for zero-latency access decisions and continued operation during network outages.

## Key Features

- **Real-Time Access Control**: RFID card readers provide sub-100ms access decisions using local cache
- **Centralized Administration**: Flask-based web dashboard for user and card management
- **Cloud-Local Synchronization**: Automatic two-way sync between Supabase cloud and local SQLite databases
- **Real-Time Restrictions**: Supabase Realtime WebSocket propagates access blocks to field devices within 500ms
- **Offline Operation**: Local devices continue functioning independently if cloud connection is lost
- **Audit Trail**: Complete event logging of all access attempts (authorized and denied)
- **Role-Based Access**: Administrative interface restricts sensitive operations to authorized users
- **Multi-Room Support**: Block access by card and room combination for granular control

### Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Cloud Database | Supabase (PostgreSQL) | Authoritative data store, REST API |
| Realtime Communication | Supabase Realtime WebSocket | Push notifications to edge devices |
| Frontend Framework | Flask + Jinja2 | Web interface for administration |
| Authentication | Supabase Auth / JWT | User authentication and session management |
| Local Database | SQLite | Edge device cache and offline storage |
| Hardware Interface | GPIO (RPi.GPIO) | Relay control for door locks |
| Serial Communication | PySerial | UART communication with RFID readers |
| Background Tasks | Threading + exponential backoff | Worker for sync and Realtime listeners |

## Installation

### Prerequisites

- Python 3.8+
- Raspberry Pi 4+ (for field deployment) or Linux host
- Supabase project with configured authentication
- UART-connected RFID reader (e.g., Mifare reader)
- GPIO-controlled electronic lock

### Cloud Setup

1. Create a Supabase project
2. Execute the database schema
3. Configure authentication with email/password
4. Enable Realtime for `access_blocks` table

### Frontend Installation

```bash
cd frontend
pip install -r requirements.txt

# Create .env file (copy from frontend/.env.example if available)
cat > .env << EOF
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=sb_publishable_your-anon-public-key
FLASK_SECRET_KEY=change-this-to-a-random-secret-key
DEBUG=false
PORT=5000
EOF

flask run
```

Access at `http://localhost:5000`

**Configuration Notes**:
- `SUPABASE_KEY`: Use the **Publishable/Anon key** from Supabase Dashboard → API keys
- Never use Service Role key in frontend (security risk)
- `FLASK_SECRET_KEY`: Generate a strong random key for session encryption

### Raspberry Pi Installation

```bash
cd raspberry
pip install -r requirements.txt

# Create .env file from template
cp .env.example .env

# Edit .env with your configuration
nano .env
# Required fields:
#   - SUPABASE_URL
#   - SUPABASE_ANON_KEY
#   - SUPABASE_EMAIL (device service account)
#   - SUPABASE_PASSWORD (device service account password)
#   - LOCAL_DB (path to SQLite database)

python main.py
```

The local server runs on port 5001 and the RFID reader/worker threads start automatically.

**Important**: 
- Create a dedicated user account in Supabase Auth for each device
- Store `.env` securely; add to `.gitignore`
- Device validates credentials on startup