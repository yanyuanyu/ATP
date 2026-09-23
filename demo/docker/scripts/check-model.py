"""Probe streaming tool calls and tool-result roundtrip without exposing credentials.

Run with Python 3.11+: python scripts/check-model.py
Reads ../.env relative to this script. Makes two small billable model requests.
"""
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    config = {}
    for line in (Path(__file__).resolve().parents[1] / '.env').read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        config[key.strip()] = value.strip().strip('\"\'')
    for name in ('LLM_API_BASE', 'LLM_API_MODEL', 'LLM_API_KEY'):
        if not config.get(name):
            raise ValueError(f'Missing {name}')
    base = config['LLM_API_BASE'].rstrip('/')
    if urllib.parse.urlsplit(base).hostname != 'www.dmxapi.cn':
        raise ValueError('This probe expects the configured DMXAPI host www.dmxapi.cn')
    if not base.startswith('https://'):
        raise ValueError('HTTPS is required')
    url = base + '/' + config.get('LLM_API_PATH', '/chat/completions').lstrip('/')
    print('Configuration: required fields present; key not displayed.', flush=True)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect())

    def request(payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + config['LLM_API_KEY'],
        })
        return opener.open(req, timeout=60)

    messages = [{'role': 'user', 'content': 'Call demo_lookup with city Paris. Do not invent a result. After receiving the tool result, report its verification code.'}]
    common = {'model': config['LLM_API_MODEL'], 'max_tokens': 300,
              'enable_thinking': False, 'parallel_tool_calls': False}
    tool = {'type': 'function', 'function': {'name': 'demo_lookup',
        'description': 'Get the demo verification code for a city.',
        'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}},
                       'required': ['city'], 'additionalProperties': False}}}
    calls = {}
    content = ''
    done = False
    with request({**common, 'messages': messages, 'tools': [tool], 'stream': True}) as response:
        for raw in response:
            line = raw.decode('utf-8').strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                done = True
                break
            event = json.loads(data)
            if 'error' in event:
                raise ValueError('Provider returned a stream error')
            for choice in event.get('choices', []):
                delta = choice.get('delta', {})
                content += delta.get('content') or ''
                for chunk in delta.get('tool_calls', []):
                    call = calls.setdefault(chunk['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    call['id'] += chunk.get('id') or ''
                    for field in ('name', 'arguments'):
                        call['function'][field] += chunk.get('function', {}).get(field) or ''
    if not done or len(calls) != 1:
        raise ValueError('Expected one complete streaming tool call')
    call = next(iter(calls.values()))
    if not call['id'] or call['function']['name'] != 'demo_lookup' or json.loads(call['function']['arguments']) != {'city': 'Paris'}:
        raise ValueError('Tool name or arguments failed validation')
    print('PASS: streaming tool call and structured arguments.', flush=True)
    messages += [{'role': 'assistant', 'content': content or None, 'tool_calls': [call]},
                 {'role': 'tool', 'tool_call_id': call['id'], 'content': '{"verification_code":"ATP-DEMO-7429"}'}]
    with request({**common, 'messages': messages, 'stream': False}) as response:
        result = json.load(response)
    answer = result['choices'][0]['message'].get('content') or ''
    if 'ATP-DEMO-7429' not in answer:
        raise ValueError('Model did not report the supplied tool result')
    print('PASS: tool result roundtrip. This does not verify the full ATP/Pi stack.')


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f'FAIL: HTTP {exc.code}; response body withheld to protect credentials.', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f'FAIL: {type(exc).__name__}; check configuration, connectivity and model compatibility.', file=sys.stderr)
        sys.exit(1)
