import os, base64, requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CONSUMER_KEY      = os.environ.get('MPESA_CONSUMER_KEY')
CONSUMER_SECRET   = os.environ.get('MPESA_CONSUMER_SECRET')
SHORTCODE         = os.environ.get('MPESA_SHORTCODE', '4136181')
PASSKEY           = os.environ.get('MPESA_PASSKEY')
CALLBACK_BASE_URL = os.environ.get('CALLBACK_BASE_URL')
MPESA_ENV         = os.environ.get('MPESA_ENV', 'sandbox')

BASE_URL = (
    'https://sandbox.safaricom.co.ke' if MPESA_ENV == 'sandbox'
    else 'https://api.safaricom.co.ke'
)

txns = {}

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

def get_token():
    auth = base64.b64encode(f"{CONSUMER_KEY}:{CONSUMER_SECRET}".encode()).decode()
    r = requests.get(f"{BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
                     headers={"Authorization": f"Basic {auth}"}, timeout=10)
    r.raise_for_status()
    return r.json()['access_token']

def make_password():
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    raw = f"{SHORTCODE}{PASSKEY}{ts}"
    return base64.b64encode(raw.encode()).decode(), ts

@app.route('/stk-push', methods=['POST'])
def stk_push():
    d = request.get_json()
    phone     = d.get('phone')
    amount    = d.get('amount')
    name      = d.get('name', 'Member')
    id_number = d.get('id_number', '')
    tier      = d.get('tier', 'WRC Access')

    if not phone or not amount:
        return jsonify({'error': 'Phone and amount required'}), 400

    try:
        password, ts = make_password()
        token = get_token()
        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": ts,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": f"{CALLBACK_BASE_URL}/callback",
            "AccountReference": "WRC",
            "TransactionDesc": f"{name} - {tier}"
        }
        r = requests.post(
            f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15
        )
        result = r.json()
        if result.get('ResponseCode') != '0':
            return jsonify({'error': result.get('ResponseDescription', 'STK Push failed')}), 400

        cid = result['CheckoutRequestID']
        txns[cid] = {
            'status': 'pending', 'phone': phone, 'amount': amount,
            'name': name, 'id_number': id_number, 'tier': tier,
            'created': datetime.utcnow().isoformat()
        }
        return jsonify({'checkout_request_id': cid})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/callback', methods=['POST'])
def callback():
    data = request.get_json(silent=True) or {}
    try:
        stk = data['Body']['stkCallback']
        cid = stk['CheckoutRequestID']
        code = stk['ResultCode']
        if cid in txns:
            if code == 0:
                items = {i['Name']: i['Value'] for i in stk['CallbackMetadata']['Item']}
                receipt = items.get('MpesaReceiptNumber', 'N/A')
                txns[cid].update({
                    'status': 'completed',
                    'receipt': receipt,
                    'paid_at': str(items.get('TransactionDate', ''))
                })
                t = txns[cid]
                send_telegram(
                    f"✅ <b>WRC PAYMENT RECEIVED</b>\n\n"
                    f"👤 <b>Name:</b> {t['name']}\n"
                    f"🪪 <b>ID:</b> {t['id_number']}\n"
                    f"📱 <b>Phone:</b> {t['phone']}\n"
                    f"🎟 <b>Tier:</b> {t['tier']}\n"
                    f"💰 <b>Amount:</b> KES {t['amount']:,}\n"
                    f"🧾 <b>M-Pesa Ref:</b> {receipt}\n"
                    f"🕐 <b>Time:</b> {datetime.now().strftime('%d %b %Y %H:%M:%S')}"
                )
                print(f"✅ {cid} | {receipt} | {t['name']}")
            else:
                txns[cid].update({'status': 'failed', 'message': stk.get('ResultDesc', 'Failed')})
                print(f"❌ {cid} | {stk.get('ResultDesc')}")
    except Exception as e:
        print(f"Callback error: {e}")
    return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})

@app.route('/payment-status/<cid>', methods=['GET'])
def status(cid):
    t = txns.get(cid, {})
    return jsonify({
        'status': t.get('status', 'pending'),
        'receipt': t.get('receipt', ''),
        'message': t.get('message', '')
    })

@app.route('/members', methods=['GET'])
def members():
    key = request.args.get('key')
    if key != os.environ.get('ADMIN_KEY', 'changeme'):
        return jsonify({'error': 'Unauthorized'}), 401
    completed = [v for v in txns.values() if v.get('status') == 'completed']
    return jsonify({'count': len(completed), 'members': completed})

@app.route('/health')
def health():
    return jsonify({'ok': True, 'env': MPESA_ENV, 'shortcode': SHORTCODE})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
