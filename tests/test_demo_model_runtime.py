"""Controller lifecycle tests; install the demo's docker SDK to run these."""
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip('docker')


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv('ATP_TRACES_DIR', str(tmp_path))
    path = Path(__file__).parents[1] / 'demo/docker/demo/controller.py'
    spec = importlib.util.spec_from_file_location('demo_model_controller_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize('finishes', [True, False])
async def test_service_audit_drains_late_response_and_has_a_deadline(controller, monkeypatch, finishes):
    controller._chat_state.update(run_id='audit-run', agent_audit=[])
    stop = asyncio.Event()
    stop.set()
    sweeps = 0
    inbound = {'ts': 'one', 'role': 'bill', 'agent_id': 'bill@payment.test',
               'request_id': 'delivery', 'type': 'agent_event', 'event': 'atp_input'}
    completed = {**inbound, 'ts': 'two', 'type': 'prompt_result', 'status': 'completed', 'text': 'done'}

    def logs(service, since):
        nonlocal sweeps
        if service != 'agent-bill':
            return []
        sweeps += 1
        return [inbound, completed] if finishes and sweeps >= 2 else [inbound]

    monkeypatch.setattr(controller, '_service_agent_log_events_sync', logs)
    await asyncio.wait_for(
        controller._watch_service_agent_audit('audit-run', 0, stop, drain_seconds=0.05),
        timeout=1,
    )
    rows = controller._chat_state['agent_audit']
    assert [row['kind'] for row in rows] == (['atp_input', 'response'] if finishes else ['atp_input'])
    assert sweeps >= 2


@pytest.mark.asyncio
async def test_timeout_closes_runtime_and_removes_pending(controller, monkeypatch):
    runtime = controller.PiTravelRuntime()
    runtime.ensure_started = AsyncMock()
    runtime.process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()))
    runtime.close = AsyncMock()

    async def timeout(future, timeout):
        future.cancel()
        raise TimeoutError()

    monkeypatch.setattr(controller.asyncio, 'wait_for', timeout)
    with pytest.raises(TimeoutError):
        await runtime.prompt('req', 'hello')
    runtime.close.assert_awaited_once()
    assert runtime.pending == {}
    assert controller.user_error(TimeoutError())['code'] == 'TASK_TIMEOUT'
    assert 'secret' not in controller.user_error(RuntimeError('secret'))['message']


@pytest.mark.asyncio
async def test_ready_is_not_model_connected_and_late_results_are_ignored(controller):
    import json
    runtime = controller.PiTravelRuntime()
    reader = asyncio.StreamReader()
    for event in [
        {'type': 'runtime_ready', 'model': 'test'},
        {'type': 'agent_event', 'request_id': 'expired', 'event': 'tool_start', 'tool': 'atp_send'},
        {'type': 'prompt_result', 'request_id': 'expired', 'status': 'completed', 'text': 'fake success'},
    ]:
        reader.feed_data((json.dumps(event) + '\n').encode())
    reader.feed_eof()
    runtime.process = SimpleNamespace(stdout=reader)
    await runtime._read_stdout()
    assert controller._chat_state['runtime']['connection'] == 'unverified'
    assert controller._chat_state['activities'] == []


@pytest.mark.asyncio
async def test_report_records_evidence_model_tools_and_result(controller):
    import json
    evidence = {'search_received': True, 'price_count': 3, 'payment_approved': True}
    controller._chat_state['messages'] = [{'id': 'assistant-req'}]
    controller._chat_state['activities'] = [{'request_id': 'req', 'tool': 'atp_receive', 'status': 'completed'}]
    controller._prepare_packet_capture = AsyncMock()
    controller._start_packet_probe = AsyncMock()
    controller._emit = AsyncMock()
    controller._watch_service_agent_audit = AsyncMock()
    controller._append_agent_audit = Mock()
    controller._pi_travel_runtime.prompt = AsyncMock(return_value={'text': '已完成模拟付款', 'evidence': evidence})
    await controller._run_chat_prompt('req', 'prompt', 'run-test')
    report = json.loads((controller.TRACES_DIR / 'run-test.report.json').read_text(encoding='utf-8'))
    assert report['evidence'] == evidence
    assert report['tool_calls'] == 1
    assert report['model']
    assert report['status'] == 'idle'
