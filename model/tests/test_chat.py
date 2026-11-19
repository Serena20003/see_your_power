"""Exercise actual API routes with external Supabase and IBM HTTP boundaries mocked."""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from service import main
from service.chat import Decision, apply_operations

LOCATION = 'cccccccc-0000-0000-0000-000000000003'
USER = 'aaaaaaaa-0000-0000-0000-000000000001'
ACTION = {'id': 'step-1', 'title': 'Charge phone', 'title_en': 'Charge phone',
          'when': 'night_before', 'reason': 'Stay in touch.', 'reason_en': 'Stay in touch.', 'completed': True}
BASE = {'id': LOCATION, 'name': 'Home', 'description': None, 'additional_notes': 'Work from home',
        'fips_code': None, 'electronic_items': [{'name': 'CPAP machine', 'number_owned': 2}, {'name': 'Phone', 'number_owned': 1}],
        'plan_items': [ACTION], 'assistant_revision': 7}


@pytest.fixture
def setup_api(monkeypatch):
    seen = {'patches': [], 'messages': [], 'row': copy.deepcopy(BASE), 'decision': {'reply': 'I can help with that.', 'operations': []}, 'status': 200}
    def handle(request):
        path = request.url.path
        if request.url.host == 'iam.cloud.ibm.com':
            return httpx.Response(200, json={'access_token': 'iam-test'})
        if path == '/ml/v1/text/chat':
            seen['messages'] = json.loads(request.content)['messages']
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(seen['decision'])}}]})
        assert request.headers['authorization'] == 'Bearer resident-token'
        assert request.headers['apikey'] == 'anon-key'
        if path == '/auth/v1/user':
            return httpx.Response(200, json={'id': USER})
        if path == '/rest/v1/users':
            assert request.url.params['id'] == f'eq.{USER}'
            return httpx.Response(200, json=[{'first_name': 'Test', 'last_name': 'Resident'}])
        assert path == '/rest/v1/locations'
        assert request.url.params['id'] == f'eq.{LOCATION}'
        assert request.url.params['user_id'] == f'eq.{USER}'
        if request.method == 'PATCH':
            assert request.url.params['assistant_revision'] == 'eq.7'
            patch = json.loads(request.content)
            seen['patches'].append(patch)
            if seen['status'] != 200:
                return httpx.Response(seen['status'], json={'message': 'not saved'})
            if seen.get('conflict'):
                return httpx.Response(200, json=[])
            seen['row'].update(patch)
            seen['row']['assistant_revision'] += 1
        return httpx.Response(200, json=[seen['row']])
    real_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    for key, value in [('SUPABASE_URL', 'https://test.supabase.co'), ('SUPABASE_ANON_KEY', 'anon-key'), ('WATSONX_API_KEY', 'test'), ('WATSONX_PROJECT_ID', 'test')]:
        monkeypatch.setenv(key, value)
    return TestClient(main.app), seen


def ask(client, message='I got rid of one CPAP machine.', **extra):
    return client.post('/v1/chat', json={'location_id': LOCATION, 'message': message, **extra}, headers={'Authorization': 'Bearer resident-token'})


def test_decrement_one_device_preserves_other_devices_actions_and_completion(setup_api):
    client, seen = setup_api
    seen['decision']['operations'] = [{'type': 'adjust_device_quantity', 'name': 'CPAP machine', 'delta': -1}]
    response = ask(client)
    assert response.status_code == 200
    assert response.json()['saved'] is True
    assert response.json()['revision'] == 8
    assert seen['patches'] == [{'electronic_items': [{'name': 'CPAP machine', 'number_owned': 1}, {'name': 'Phone', 'number_owned': 1}]}]
    assert response.json()['plan_items'] == [ACTION]
    assert seen['messages'][-1]['content'] == 'I got rid of one CPAP machine.'
    assert 'CPAP machine' in seen['messages'][1]['content']
    assert 'Work from home' in seen['messages'][1]['content']


def test_question_uses_saved_context_and_history_without_writing(setup_api):
    client, seen = setup_api
    response = ask(client, 'What about my phone?', history=[{'role': 'user', 'text': 'What should I prioritize?'}, {'role': 'assistant', 'text': 'Review backup options.'}])
    assert response.status_code == 200
    assert response.json()['saved'] is False
    assert seen['patches'] == []
    assert seen['messages'][-2] == {'role': 'assistant', 'content': 'Review backup options.'}
    assert 'step-1' in seen['messages'][1]['content']


@pytest.mark.parametrize('operation', [
    {'type': 'update_address', 'address': 'New address'},
    {'type': 'set_profile', 'first_name': 'Other'},
    {'type': 'set_contact', 'email': 'other@example.test'},
    {'type': 'set_device_quantity', 'name': 'Phone', 'quantity': 2, 'address': 'Injected'},
])
def test_profile_and_contact_writes_are_rejected_even_if_model_requests_them(setup_api, operation):
    client, seen = setup_api
    seen['decision']['operations'] = [operation]
    response = ask(client, 'Change my address')
    assert response.status_code == 200
    assert response.json()['saved'] is False
    assert seen['patches'] == []


def test_appending_action_preserves_existing_checked_steps(setup_api):
    client, seen = setup_api
    item = {**{k: v for k, v in ACTION.items() if k not in ('id', 'completed')}, 'title': 'Review CPAP backup options', 'title_en': 'Review CPAP backup options'}
    seen['decision']['operations'] = [{'type': 'add_action', 'item': item}]
    response = ask(client, 'Add a step to review CPAP backup options')
    assert response.status_code == 200
    actions = response.json()['plan_items']
    assert actions[0] == ACTION
    assert actions[1]['title'] == item['title']
    assert actions[1]['completed'] is False
    assert actions[1]['id'] != ACTION['id']


def test_edit_then_remove_action_without_losing_others():
    location = copy.deepcopy(BASE)
    location['plan_items'].append({**ACTION, 'id': 'step-2', 'completed': False})
    decision = Decision.model_validate({'reply': 'Update step.', 'operations': [
        {'type': 'update_action', 'id': 'step-1', 'item': {'title': 'Charge phone early', 'title_en': 'Charge phone early', 'when': 'two_days_before', 'reason': 'Prepare.', 'reason_en': 'Prepare.'}},
        {'type': 'remove_action', 'id': 'step-2'},
    ]})
    patch = apply_operations(location, decision.operations)
    assert len(patch['plan_items']) == 1
    assert patch['plan_items'][0]['completed'] is True
    assert patch['plan_items'][0]['title'] == 'Charge phone early'
    assert location['plan_items'][0] == ACTION  # no partial in-memory mutation


def test_invalid_later_operation_prevents_entire_write(setup_api):
    client, seen = setup_api
    seen['decision']['operations'] = [{'type': 'adjust_device_quantity', 'name': 'CPAP machine', 'delta': -1}, {'type': 'remove_action', 'id': 'missing'}]
    response = ask(client)
    assert response.json()['saved'] is False
    assert seen['patches'] == []


def test_save_failure_is_not_reported_as_success(setup_api):
    client, seen = setup_api
    seen['decision']['operations'] = [{'type': 'set_device_quantity', 'name': 'Laptop', 'quantity': 1}]
    seen['status'] = 500
    response = ask(client, 'Add one laptop')
    assert response.status_code == 502
    assert 'saved' not in response.json()


def test_concurrent_edit_returns_conflict_without_overwrite(setup_api):
    client, seen = setup_api
    seen['decision']['operations'] = [{'type': 'set_device_quantity', 'name': 'Laptop', 'quantity': 1}]
    seen['conflict'] = True
    response = ask(client, 'Add one laptop')
    assert response.status_code == 409
    assert seen['row'] == BASE


def test_checkbox_completion_is_saved(setup_api):
    client, seen = setup_api
    response = client.post(f'/v1/chat/complete/{LOCATION}', json={'id': 'step-1', 'completed': False, 'revision': 7}, headers={'Authorization': 'Bearer resident-token'})
    assert response.status_code == 200
    assert response.json()['plan_items'][0]['completed'] is False


def test_chat_requires_sign_in():
    response = TestClient(main.app).post('/v1/chat', json={'location_id': LOCATION, 'message': 'Add phone'})
    assert response.status_code == 401


def test_client_cannot_supply_profile_or_household_context(setup_api):
    client, seen = setup_api
    response = ask(client, profile={'first_name': 'Injected'}, electronic_items=[])
    assert response.status_code == 422
    assert not seen['messages']


def test_no_operation_cannot_claim_that_contact_information_was_saved(setup_api):
    client, seen = setup_api
    seen['decision'] = {'reply': "I've updated your address.", 'operations': []}
    response = ask(client, 'What is my saved address?')
    assert response.json()['saved'] is False
    assert "I've updated" not in response.json()['reply']
    assert seen['patches'] == []


def test_negative_quantity_is_rejected_atomically(setup_api):
    client, seen = setup_api
    seen['decision']['operations'] = [{'type': 'adjust_device_quantity', 'name': 'Phone', 'delta': -2}]
    response = ask(client, 'Remove two phones')
    assert response.json()['saved'] is False
    assert seen['patches'] == []


def test_other_users_location_cannot_be_read_or_changed(setup_api, monkeypatch):
    client, seen = setup_api
    from service.chat import HouseholdStore
    original = HouseholdStore.request
    async def invisible(self, method, path, **kwargs):
        if path == '/rest/v1/locations':
            return []
        return await original(self, method, path, **kwargs)
    monkeypatch.setattr(HouseholdStore, 'request', invisible)
    response = ask(client)
    assert response.status_code == 404
    assert seen['patches'] == []
    assert seen['messages'] == []


def test_busy_model_reports_service_issue_without_saving(setup_api, monkeypatch):
    client, seen = setup_api
    async def busy(_messages):
        request = httpx.Request('POST', 'https://example.test/chat')
        response = httpx.Response(429, request=request)
        response.raise_for_status()
    monkeypatch.setattr(main, '_watsonx_json', busy)
    response = ask(client)
    assert response.status_code == 200
    assert response.json()['saved'] is False
    assert 'temporarily busy' in response.json()['reply']
    assert seen['patches'] == []


def test_reload_does_not_regenerate_existing_plan_or_reset_completion(setup_api):
    client, seen = setup_api
    response = client.post(f'/v1/chat/initialize/{LOCATION}', headers={'Authorization': 'Bearer resident-token'})
    assert response.status_code == 200
    assert response.json()['plan_items'] == [ACTION]
    assert response.json()['initialized'] is True
    assert seen['patches'] == []
    assert seen['messages'] == []


def test_intentionally_empty_plan_stays_empty(setup_api):
    client, seen = setup_api
    seen['row']['plan_items'] = []
    response = client.post(f'/v1/chat/initialize/{LOCATION}', headers={'Authorization': 'Bearer resident-token'})
    assert response.json()['plan_items'] == []
    assert response.json()['initialized'] is True
    assert seen['patches'] == []
    assert seen['messages'] == []


def test_device_creation_and_removal_preserve_unrelated_inventory():
    decision = Decision.model_validate({'reply': 'Update inventory.', 'operations': [
        {'type': 'set_device_quantity', 'name': 'Laptop', 'quantity': 2},
        {'type': 'set_device_quantity', 'name': 'CPAP machine', 'quantity': 0},
    ]})
    patch = apply_operations(BASE, decision.operations)
    assert patch == {'electronic_items': [{'name': 'Phone', 'number_owned': 1}, {'name': 'Laptop', 'number_owned': 2}]}
    assert BASE['electronic_items'][0]['number_owned'] == 2


def test_parallel_initialization_loads_winning_saved_plan(setup_api, monkeypatch):
    from fastapi import HTTPException
    from service.chat import HouseholdStore
    client, seen = setup_api
    seen['row']['plan_items'] = None
    async def generate(_request):
        # Another browser finishes initialization while this request generates.
        seen['row']['plan_items'] = [ACTION]
        seen['row']['assistant_revision'] = 8
        return main.PlanResponse(reply='Plan', items=[], source='fallback')
    async def conflict(*_args):
        raise HTTPException(409, 'Concurrent edit')
    monkeypatch.setattr(main, 'post_plan', generate)
    monkeypatch.setattr(HouseholdStore, 'save', conflict)
    response = client.post(f'/v1/chat/initialize/{LOCATION}', headers={'Authorization': 'Bearer resident-token'})
    assert response.status_code == 200
    assert response.json()['plan_items'] == [ACTION]
    assert response.json()['revision'] == 8
