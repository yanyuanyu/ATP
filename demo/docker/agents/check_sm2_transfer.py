"""Run inside server-family: real cross-domain SM2 accept/reject checks.

Sends one synthetic message to a dedicated, inactive test mailbox. No LLM call.
"""
import asyncio
from pathlib import Path

import httpx

from atp.core.message import ATPMessage
from atp.core.signature import Signer
from atp.discovery.dns import DNSResolver
from atp.security.atk import ATKRecord
from atp.storage.keys import KeyStorage


async def main():
    keys = KeyStorage(Path('/root/.atp/keys'))
    private, public = keys.load_key_pair('default', 'sm2')
    record = ATKRecord.parse(await DNSResolver().query_txt('default.atk._atp.family.test'))
    assert record.algorithm == 'sm2' and record.get_public_key() == public
    signer = Signer(private, 'default', 'family.test')

    def signed():
        return signer.sign(ATPMessage.create('audit@family.test', 'audit@hotel.test',
                                            {'subject': 'sm2-regression', 'body': 'original'})).to_dict()

    url = 'https://server-hotel.hotel.test:7443/.well-known/atp/v1/message'
    async with httpx.AsyncClient(timeout=20) as client:
        original = signed()
        response = await client.post(url, json=original)
        assert response.status_code == 202, response.text
        response = await client.post(url, json=original)
        assert response.status_code == 400 and response.json()['error'] == 'Replay detected', response.text
        tampered = signed()
        tampered['payload']['body'] = 'tampered'
        response = await client.post(url, json=tampered)
        assert response.status_code == 403 and response.json()['error'] == 'ATK verification failed', response.text
        mismatched = signed()
        mismatched['signature']['algorithm'] = 'ed25519'
        response = await client.post(url, json=mismatched)
        assert response.status_code == 403 and response.json()['error'] == 'ATK verification failed', response.text
    print('PASS: SM2 DNS key matches; real remote accept, replay rejection, tamper rejection, algorithm mismatch rejection.')


if __name__ == '__main__':
    asyncio.run(main())
