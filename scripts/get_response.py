import json
import sys
from urllib.request import Request, urlopen

token = '828e824b5f5c302d6f4a364d12580a73aba8fb8303ca3bdff38d9f2db12c0bb8'
base_url = 'https://bridge.acsvalentinacs.com'

# Get latest message from claude inbox
req = Request(f'{base_url}/inbox/claude?limit=1&order=desc')
req.add_header('X-HOPE-Token', token)
with urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode('utf-8'))

if data.get('messages'):
    msg = data['messages'][0]
    payload = msg.get('payload', {})
    message = payload.get('message', '') if isinstance(payload, dict) else str(payload)

    print(f"Reply-To: {msg.get('reply_to', '?')[:32]}...")
    print(f"Type: {msg.get('type')}")
    print()
    print("=== GPT RESPONSE ===")
    print(message)
else:
    print("No messages found")
