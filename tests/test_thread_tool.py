import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from hermes_email.config import EmailPluginConfig
from hermes_email.models import EmailAddress, EmailMessage
from hermes_email.plugin import EmailPlugin
from hermes_email.providers import MockEmailProvider
from hermes_email.tools import THREAD_TOOL, register_read_tools


class Context:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any):
        self.tools.append(kwargs)
        return type('H', (), {'dispose': lambda self: None})()


def message(pid: str, rid: str, refs: str = '', minute: int = 0) -> EmailMessage:
    metadata = {'rfc_message_id': rid, 'content': 'text/plain'}
    if refs:
        metadata['references'] = refs
    return EmailMessage(
        message_id=pid,
        subject='Customer project',
        sender=EmailAddress(f'{pid}@example.invalid'),
        recipients=(EmailAddress('office@example.invalid'),),
        body_text=f'content-{pid}',
        received_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        metadata=metadata,
    )


def test_thread_tool_returns_bounded_untrusted_chronology() -> None:
    root = message('root', '<root@example.invalid>')
    reply = message('reply', '<reply@example.invalid>', '<root@example.invalid>', 1)
    unrelated = message('other', '<other@example.invalid>', '', 2)
    provider = MockEmailProvider((reply, unrelated, root))
    config = EmailPluginConfig.from_mapping({'email': {'provider': 'mock', 'read_mode': 'mock'}})
    plugin = EmailPlugin(config, provider=provider)
    ctx = Context()
    register_read_tools(ctx, plugin)
    tool = next(item for item in ctx.tools if item['name'] == THREAD_TOOL)

    result = json.loads(asyncio.run(tool['handler']({'message_id': 'reply', 'body_limit': 100})))

    assert result['ok'] is True
    assert result['operation'] == 'thread'
    assert result['content_is_untrusted'] is True
    assert result['thread_basis'] == 'rfc-message-id-references'
    assert [item['message_id'] for item in result['messages']] == ['root', 'reply']
    assert all(len(item['body_text']) <= 100 for item in result['messages'])
    assert result['scan_complete'] is True


def test_thread_tool_does_not_find_missing_seed() -> None:
    provider = MockEmailProvider(())
    config = EmailPluginConfig.from_mapping({'email': {'provider': 'mock', 'read_mode': 'mock'}})
    plugin = EmailPlugin(config, provider=provider)
    ctx = Context()
    register_read_tools(ctx, plugin)
    tool = next(item for item in ctx.tools if item['name'] == THREAD_TOOL)

    result = json.loads(asyncio.run(tool['handler']({'message_id': 'missing'})))

    assert result['ok'] is True
    assert result['found'] is False
    assert result['messages'] == []
