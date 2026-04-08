from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

DB_FILE = "device_status.db"

# Tek cihaz ID
DEVICE_ID = os.environ.get("DEVICE_ID", "press_1")

# ESP32 kaç dakikada bir heartbeat atıyor
HEARTBEAT_INTERVAL_MIN = 5

# Kaç dakika gelmezse offline sayılacak
OFFLINE_TIMEOUT_MIN = 12

# Mail ayarları
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALERT_TO_EMAIL = os.environ.get("ALERT_TO_EMAIL", SMTP_USER)


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            device_id TEXT NOT NULL,
            last_seen TEXT,
            alert_sent INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("SELECT COUNT(*) as c FROM device")
    count = cur.fetchone()["c"]
    if count == 0:
        cur.execute("""
            INSERT INTO device (id, device_id, last_seen, alert_sent)
            VALUES (1, ?, NULL, 0)
        """, (DEVICE_ID,))
    conn.commit()
    conn.close()


def utc_now():
    return datetime.now(timezone.utc)


def parse_iso(dt_str):
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str)


def send_email(subject, body):
    if not SMTP_USER or not SMTP_PASSWORD or not ALERT_TO_EMAIL:
        print("Mail ayarları eksik, mail gönderilmedi.")
        return False

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_TO_EMAIL

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        print("Mail gönderildi.")
        return True
    except Exception as e:
        print(f"Mail gönderme hatası: {e}")
        return False


def get_device():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM device WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    return row


def update_last_seen():
    now_str = utc_now().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE device
        SET last_seen = ?, alert_sent = 0
        WHERE id = 1
    """, (now_str,))
    conn.commit()
    conn.close()


def mark_alert_sent():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE device SET alert_sent = 1 WHERE id = 1")
    conn.commit()
    conn.close()


def compute_status():
    row = get_device()
    last_seen = parse_iso(row["last_seen"])
    now = utc_now()

    if last_seen is None:
        return {
            "device_id": row["device_id"],
            "status": "unknown",
            "last_seen": None,
            "minutes_since_last_seen": None,
            "alert_sent": bool(row["alert_sent"])
        }

    diff = now - last_seen
    diff_minutes = diff.total_seconds() / 60.0

    if diff_minutes > OFFLINE_TIMEOUT_MIN:
        status = "offline"
    else:
        status = "online"

    return {
        "device_id": row["device_id"],
        "status": status,
        "last_seen": last_seen.isoformat(),
        "minutes_since_last_seen": round(diff_minutes, 1),
        "alert_sent": bool(row["alert_sent"])
    }


def check_and_send_offline_alert():
    info = compute_status()

    if info["status"] != "offline":
        return

    row = get_device()
    if row["alert_sent"]:
        return

    subject = f"Cihaz Offline: {info['device_id']}"
    body = (
        f"Cihaz: {info['device_id']}\n"
        f"Durum: OFFLINE\n"
        f"Son heartbeat: {info['last_seen']}\n"
        f"Geçen süre: {info['minutes_since_last_seen']} dakika\n"
        f"Eşik: {OFFLINE_TIMEOUT_MIN} dakika\n"
    )

    sent = send_email(subject, body)
    if sent:
        mark_alert_sent()


@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "message": "Server çalışıyor",
        "heartbeat_endpoint": "/heartbeat",
        "status_endpoint": "/status",
        "dashboard_endpoint": "/dashboard"
    })


@app.route("/heartbeat", methods=["GET", "POST"])
def heartbeat():
    # İstersen device_id kontrolü koyabilirsin
    # Tek cihaz olduğu için direkt kabul ediyoruz
    update_last_seen()
    return jsonify({
        "ok": True,
        "message": "heartbeat alındı",
        "time": utc_now().isoformat()
    })


@app.route("/status", methods=["GET"])
def status():
    check_and_send_offline_alert()
    return jsonify(compute_status())


@app.route("/dashboard", methods=["GET"])
def dashboard():
    check_and_send_offline_alert()
    info = compute_status()

    if info["status"] == "online":
        color = "green"
        text = "ONLINE"
    elif info["status"] == "offline":
        color = "red"
        text = "OFFLINE"
    else:
        color = "gray"
        text = "UNKNOWN"

    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="30">
        <title>Cihaz Durumu</title>
    </head>
    <body style="font-family: Arial; padding: 30px;">
        <h2>Makine Durumu</h2>
        <table border="1" cellpadding="10" cellspacing="0">
            <tr>
                <th>Cihaz</th>
                <th>Durum</th>
                <th>Son Görülme</th>
                <th>Geçen Süre</th>
            </tr>
            <tr>
                <td>{info["device_id"]}</td>
                <td style="color:{color}; font-weight:bold;">{text}</td>
                <td>{info["last_seen"] if info["last_seen"] else "-"}</td>
                <td>{str(info["minutes_since_last_seen"]) + " dk" if info["minutes_since_last_seen"] is not None else "-"}</td>
            </tr>
        </table>

        <p style="margin-top:20px;">
            Heartbeat aralığı: {HEARTBEAT_INTERVAL_MIN} dk<br>
            Offline timeout: {OFFLINE_TIMEOUT_MIN} dk
        </p>
    </body>
    </html>
    """
    return html


# Render açılışında DB hazırla
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
