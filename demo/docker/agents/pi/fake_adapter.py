"""Offline runtime-test fixture; never connects to an ATP server."""
import json
import os
import sys

pending = []
print(json.dumps({'type': 'adapter_ready'}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    params = request.get('params', {})
    if request['method'] == 'send':
        subject = params['subject']
        nonce = 'request-' + subject
        if subject == 'search':
            bodies = [{'kind': 'search-result', 'city': 'Paris', 'nights': 2, 'matches': [{'hotel': 'ParisGarden'}]}]
        elif subject == 'subscribe':
            bodies = [{'kind': 'price-change', 'hotel': 'ParisGarden', 'event_index': i, 'total_events': 3, 'price': price, 'currency': 'USD'} for i, price in enumerate([180, 170, 190], 1)]
        else:
            bodies = [{'kind': 'payment-result', 'hotel': 'ParisGarden', 'nights': 2, 'nightly_amount': 170, 'total': 340, 'currency': 'USD', 'simulation': True, 'approved': True, 'authorization_id': 'sim-test'}]
        pending = [{'nonce': f'{nonce}-{i}', 'from': params['to'], 'to': 'travel@family.test', 'task_id': params['task_id'], 'context_id': params['context_id'], 'in_reply_to': nonce, 'body': json.dumps(body)} for i, body in enumerate(bodies)]
        result = {'nonce': nonce, 'status': 'accepted'}
    else:
        result = [] if os.getenv('FAKE_NO_REPLY') else pending
        pending = []
    print(json.dumps({'id': request['id'], 'ok': True, 'result': result}), flush=True)
