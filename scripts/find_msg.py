import json
from urllib.request import Request, urlopen

token = '828e824b5f5c302d6f4a364d12580a73aba8fb8303ca3bdff38d9f2db12c0bb8'
base_url = 'https://bridge.acsvalentinacs.com'

# Find my message in GPT inbox
my_id = 'sha256:a21239ed'

req = Request(f'{base_url}/inbox/gpt?limit=200&order=desc')
req.add_header('X-HOPE-Token', token)
with urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode('utf-8'))

found = False
for msg in data.get('messages', []):
    if my_id in msg.get('id', ''):
        print('FOUND MY MESSAGE!')
        print(json.dumps(msg, indent=2, ensure_ascii=False))
        found = True
        break

if not found:
    print(f'Message not found. Total: {len(data.get("messages", []))}')
    # Show first message to see format
    if data.get('messages'):
        print('First message:', data['messages'][0].get('id', '?')[:30])
