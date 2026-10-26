import os, hmac, hashlib, requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://wrcprivate.club", "https://www.wrcprivate.club"])

PAYSTACK_SECRET = os.environ.get('PAYSTACK_SECRET_KEY', '')
SITE_URL = os.environ.get('SITE_URL', 'https://wrcprivate.club')

# Prices live HERE, never in the browser. Amounts are in cents (USD).
TIERS = {
    TIERS = {
    'prive':  {'label': 'Privé Access',       'amount': 1000},
    'circle': {'label': 'Inner Circle Pass',  'amount': 2000},
    'elite':  {'label': 'Elite Pass',         'amount': 5000},
}
}

def send_telegram(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        print(f"Telegram error: {e}")

@app.route('/pay', methods=['POST'])
def pay():
    d = request.get_json(silent=True) or {}
    name = (d.get('full_name') or '').strip()
    email = (d.get('email') or '').strip()
    tier_key = d.get('tier')

    if tier_key not in TIERS:
        return jsonify({'error': 'Invalid tier'}), 400
    if not name or '@' not in email:
        return jsonify({'error': 'Name and a valid email are required'}), 400

    tier = TIERS[tier_key]
    try:
        r = requests.post(
            'https://api.paystack.co/transaction/initialize',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET}'},
            json={
                'email': email,
                'amount': tier['amount'],
                'currency': 'USD',
                'callback_url': f'{SITE_URL}/?paid=1',
                'metadata': {'name': name, 'tier': tier['label']},
            },
            timeout=15
        )
        res = r.json()
        if not res.get('status'):
            return jsonify({'error': res.get('message', 'Could not start payment')}), 400
        return jsonify({'success': True, 'authorization_url': res['data']['authorization_url']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    raw = request.get_data()
    sig = request.headers.get('x-paystack-signature', '')
    expected = hmac.new(PAYSTACK_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return 'invalid signature', 401

    event = request.get_json(silent=True) or {}
    if event.get('event') == 'charge.success':
        d = event['data']
        meta = d.get('metadata') or {}
        send_telegram(
            f"✅ <b>WRC PAYMENT RECEIVED</b>\n\n"
            f"👤 <b>Name:</b> {meta.get('name', 'N/A')}\n"
            f"📧 <b>Email:</b> {d['customer']['email']}\n"
            f"🎟 <b>Tier:</b> {meta.get('tier', 'N/A')}\n"
            f"💰 <b>Amount:</b> ${d['amount'] / 100:,.2f} {d['currency']}\n"
            f"🧾 <b>Ref:</b> {d['reference']}\n"
            f"🕐 <b>Time:</b> {datetime.now().strftime('%d %b %Y %H:%M:%S')}"
        )
    return 'ok', 200

@app.route('/health')
def health():
    return jsonify({'ok': True, 'paystack_key_set': bool(PAYSTACK_SECRET)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)