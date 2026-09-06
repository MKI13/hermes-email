"""Explicit independent-provider lab; disposable GreenMail, no external mail.

Run deliberately, never at plugin import. Requires Docker and the pinned image.
All test accounts are synthetic. GreenMail is isolated on an internal Docker
network without published ports. Loopback-only temporary forwarders reach only
the container created here; TLS remains end to end across these forwarders.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
import hashlib
import imaplib
import json
import os
from pathlib import Path
import re
import secrets
import select
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_email.approval import ApprovalScope
from hermes_email.config import EmailPluginConfig
from hermes_email.draft_storage import SqliteDraftStore
from hermes_email.draft_tools import CREATE_REPLY_ALL_DRAFT_TOOL, register_draft_tools
from hermes_email.plugin import EmailPlugin
from hermes_email.providers import ImapReadOnlyProvider, ProviderTlsError, ProviderAuthenticationError
from hermes_email.send_workflow import ReviewedSendWorkflow
from hermes_email.secrets import EnvironmentSecretResolver
from hermes_email.smtp import (SmtplibTransport, SmtpSubmission, SmtpTlsError,
                               SmtpAuthenticationError)

IMAGE = 'greenmail/standalone@sha256:3df66b7edd01c8a301343ca5e3601d8674760d4708655573560c24745e624fb2'


def command(*args: str, timeout: int = 60) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=timeout).strip()
    except (subprocess.SubprocessError, OSError):
        # Even synthetic passwords in Docker arguments must not enter CI logs.
        raise RuntimeError("independent lab command failed") from None


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(self.server.target, timeout=5) as upstream:
                self.request.settimeout(10)
                upstream.settimeout(10)
                sockets = (self.request, upstream)
                while True:
                    ready, _, _ = select.select(sockets, [], [], 30)
                    if not ready:
                        return
                    for incoming in ready:
                        data = incoming.recv(65536)
                        if not data:
                            return
                        outgoing = upstream if incoming is self.request else self.request
                        outgoing.sendall(data)
        except (OSError, TimeoutError):
            return


@contextlib.contextmanager
def local_port(ip: str, port: int):
    with Forwarder(('127.0.0.1', 0), ForwardHandler) as server:
        server.target = (ip, port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(5)


@contextlib.contextmanager
def greenmail():
    # Image acquisition is an explicit separate Docker operation, not implicit.
    command('docker', 'image', 'inspect', IMAGE)
    name = 'hermes-email-provider-proof-' + secrets.token_hex(6)
    password = secrets.token_hex(16)
    command('docker', 'network', 'create', '--internal', name)
    cid = None
    try:
        users = ','.join(f'{user}:{password}@example.invalid' for user in ('sender','customer','copy','private'))
        opts = ('-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 '
                '-Dgreenmail.tls.keystore.file=/home/greenmail/greenmail.p12 '
                '-Dgreenmail.tls.keystore.password=changeit -Dgreenmail.users=' + users)
        cid = command('docker', 'run', '-d', '--name', name, '--network', name,
            '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--memory', '512m', '--cpus', '2',
            '--pids-limit', '128', '-e', 'GREENMAIL_OPTS=' + opts, IMAGE)
        detail = json.loads(command('docker', 'inspect', cid))[0]
        ip = detail['NetworkSettings']['Networks'][name]['IPAddress']
        assert json.loads(command('docker', 'network', 'inspect', name))[0]['Internal']
        assert not detail['HostConfig'].get('PortBindings')
        for attempt in range(100):
            try:
                with socket.create_connection((ip, 3025), timeout=1) as sock:
                    if sock.recv(100).startswith(b'220'):
                        break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError('independent mail server startup failed')
        with contextlib.ExitStack() as stack:
            smtp = stack.enter_context(local_port(ip, 3465))
            imap = stack.enter_context(local_port(ip, 3143))
            imaps = stack.enter_context(local_port(ip, 3993))
            # Trust comes from the exact pinned image's own keystore, not TOFU
            # against an arbitrary existing host. No production pin is learned.
            temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='mail-certificate-')))
            bundle = temporary / 'lab.p12'
            command('docker', 'cp', cid + ':/home/greenmail/greenmail.p12', str(bundle))
            bundle.chmod(0o600)
            public = temporary / 'public.crt'
            command('openssl', 'pkcs12', '-in', str(bundle), '-passin', 'pass:changeit',
                    '-clcerts', '-nokeys', '-out', str(public))
            der = subprocess.check_output(['openssl','x509','-in',str(public),'-outform','DER'],timeout=10)
            fingerprint = hashlib.sha256(der).hexdigest()
            yield {'smtp': smtp, 'imap': imap, 'imaps': imaps, 'pin': fingerprint,
                   'password': password, 'container': cid, 'public_cert': public}
    finally:
        try:
            # The random name belongs to this invocation even if docker run
            # timed out after creating the container but before returning its ID.
            subprocess.run(['docker', 'rm', '-f', cid or name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15, check=False)
        finally:
            subprocess.run(['docker', 'network', 'rm', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15, check=True)


class RecordingResolver(EnvironmentSecretResolver):
    def __init__(self, values):
        self.calls = []
        super().__init__(lambda key: values.get(key))
    def get_secret(self, reference):
        self.calls.append(reference)
        return super().get_secret(reference)


class ToolContext:
    def __init__(self): self.tools = {}
    def register_tool(self, **entry):
        self.tools[entry['name']] = entry
        return type('Handle', (), {'dispose': lambda self: None})()


def test_provider(lab, root: Path) -> dict:
    values = {'HERMES_EMAIL_IMAP_LAB_USER': 'sender',
              'HERMES_EMAIL_IMAP_LAB_PASSWORD': lab['password'],
              'HERMES_EMAIL_SMTP_LAB_USER': 'sender',
              'HERMES_EMAIL_SMTP_LAB_PASSWORD': lab['password']}
    config = EmailPluginConfig.from_mapping({
        'hermes': {'profile': 'hermesemailtest'},
        'email': {'provider': 'imap', 'read_mode': 'readonly'},
        'imap': {'host': '127.0.0.1', 'port': lab['imaps'], 'security': 'tls-pinned',
                 'tls_sha256_fingerprint': lab['pin'], 'mailbox': 'INBOX',
                 'username_ref': 'HERMES_EMAIL_IMAP_LAB_USER',
                 'password_ref': 'HERMES_EMAIL_IMAP_LAB_PASSWORD', 'timeout_seconds': 5},
        'drafts': {'mode': 'sqlite', 'account_namespace': 'independent-lab'},
        'smtp': {'mode': 'submission', 'account_namespace': 'independent-lab',
                 'host': '127.0.0.1', 'port': lab['smtp'], 'security': 'implicit_tls_pinned',
                 'tls_sha256_fingerprint': lab['pin'], 'sender_address': 'sender@example.invalid',
                 'username_ref': 'HERMES_EMAIL_SMTP_LAB_USER',
                 'password_ref': 'HERMES_EMAIL_SMTP_LAB_PASSWORD', 'timeout_seconds': 5},
        'reply_policy': {'own_addresses': ['sender@example.invalid']},
        'recipient_policy': {'mode':'allowlist','allowed_addresses':['customer@example.invalid','copy@example.invalid']},
        'safety': {'allow_send': True},
        'send_workflow': {'mode': 'account-test'},
    })
    resolver = RecordingResolver(values)
    # Real authenticated SMTP seed, not mock mailbox injection.
    seed = (b'From: customer@example.invalid\r\nTo: sender@example.invalid\r\n'
            b'Cc: copy@example.invalid\r\nSubject: Independent provider test\r\n'
            b'Message-ID: <seed@example.invalid>\r\nReferences: <root@example.invalid>\r\n'
            b'Date: Sun, 06 Sep 2026 00:00:00 +0000\r\n\r\nSynthetic customer request.\r\n')
    seed_transport = SmtplibTransport(replace(config.smtp, sender_address='customer@example.invalid'), resolver)
    seed_transport.check_health()
    seed_transport.submit_once(SmtpSubmission('customer@example.invalid',('sender@example.invalid',),seed,1000000))
    seed_transport.close()
    provider = ImapReadOnlyProvider(config.imap, resolver)
    asyncio.run(provider.check_health())
    page = asyncio.run(provider.fetch_messages(limit=10))
    assert len(page.messages) == 1, 'test account should contain exactly one seed'
    source = page.messages[0]
    assert source.sender.address == 'customer@example.invalid'
    assert [a.address for a in source.cc] == ['copy@example.invalid']
    assert source.body_text == 'Synthetic customer request.'
    drafts = SqliteDraftStore(root/'email-drafts.sqlite3',config.drafts)
    plugin = EmailPlugin(config, provider=provider, draft_store=drafts)
    context = ToolContext(); register_draft_tools(context, plugin)
    args = {'message_id': source.message_id, 'body_text': 'Reviewed independent-provider reply.',
            'operation_id': 'independent-lab-reply-0001'}
    result = json.loads(asyncio.run(context.tools[CREATE_REPLY_ALL_DRAFT_TOOL]['handler'](args)))
    assert result['ok'] is True
    identifier = result['mutation']['draft_id']
    scope = ApprovalScope('hermesemailtest',str(root.resolve()),'synthetic-lab-user','synthetic-lab-session')
    saved = {key:os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        service = ReviewedSendWorkflow(config,drafts,root,lambda:scope)
        def presenter(display, challenge, timeout):
            reviewed = json.loads(display)
            assert reviewed['action']=='SEND THIS EMAIL ONCE'
            assert reviewed['body_text']=='Reviewed independent-provider reply.'
            return 'APPROVE '+challenge
        sent = service.run(identifier,presenter)
        assert sent['state']=='accepted' and sent['sent_copy']=='saved-local', sent
        restarted_drafts = SqliteDraftStore(root/'email-drafts.sqlite3', config.drafts)
        restarted_service = ReviewedSendWorkflow(config,restarted_drafts,root,lambda:scope)
        replay = restarted_service.run(identifier,lambda *args: (_ for _ in ()).throw(AssertionError('unexpected approval replay')))
        assert replay['replayed'] and replay['message_id']==sent['message_id']
        restarted_drafts.close()
    finally:
        for key,old in saved.items():
            if old is None:os.environ.pop(key,None)
            else:os.environ[key]=old
    recipient_values = {**values,'HERMES_EMAIL_IMAP_LAB_USER':'customer'}
    recipient = ImapReadOnlyProvider(config.imap, RecordingResolver(recipient_values))
    received = asyncio.run(recipient.fetch_messages(limit=10))
    assert len(received.messages)==1, 'duplicate or missing delivery to synthetic recipient'
    answer=received.messages[0]
    assert answer.body_text=='Reviewed independent-provider reply.'
    assert answer.metadata['in_reply_to']=='<seed@example.invalid>'
    assert answer.metadata['references']=='<root@example.invalid> <seed@example.invalid>'
    assert answer.metadata['rfc_message_id']==sent['message_id']
    copy_recipient = ImapReadOnlyProvider(config.imap, RecordingResolver({**values,
        'HERMES_EMAIL_IMAP_LAB_USER':'copy'}))
    try:
        copies = asyncio.run(copy_recipient.fetch_messages(limit=10))
        assert len(copies.messages) == 1 and copies.messages[0].metadata['rfc_message_id'] == sent['message_id']
    finally:
        copy_recipient.close()
    from email.parser import BytesParser
    from email import policy
    eml, = root.rglob('*.eml')
    parsed_copy = BytesParser(policy=policy.default).parsebytes(eml.read_bytes())
    assert parsed_copy['Bcc'] is None
    assert str(parsed_copy['Message-ID']).strip() == sent['message_id']
    # Wrong certificate denies BEFORE any credential resolution, both protocols.
    for settings, cls, action, expected in [
        (replace(config.smtp,tls_sha256_fingerprint='0'*64),SmtplibTransport,'check_health',SmtpTlsError),
        (replace(config.imap,tls_sha256_fingerprint='0'*64),ImapReadOnlyProvider,'check_health',ProviderTlsError)]:
        bad_resolver=RecordingResolver(values);instance=cls(settings,bad_resolver)
        try:
            try:
                value=getattr(instance,action)()
                if asyncio.iscoroutine(value):asyncio.run(value)
            except expected:pass
            else:raise AssertionError('wrong pin accepted')
            assert not bad_resolver.calls
        finally:instance.close()
    # Real authentication refusal, not a model guess or missing-secret shortcut.
    for cls,settings,expected in [(SmtplibTransport,config.smtp,SmtpAuthenticationError),
                                  (ImapReadOnlyProvider,config.imap,ProviderAuthenticationError)]:
        wrong={**values,'HERMES_EMAIL_SMTP_LAB_PASSWORD':'wrong-test-password',
                       'HERMES_EMAIL_IMAP_LAB_PASSWORD':'wrong-test-password'}
        instance=cls(settings,RecordingResolver(wrong))
        try:
            try:
                value=instance.check_health()
                if asyncio.iscoroutine(value):asyncio.run(value)
            except expected as error:
                assert 'wrong-test-password' not in str(error)
            else:raise AssertionError('invalid password accepted')
        finally:instance.close()
    recipient.close();plugin.close()
    return {'independent_server':'GreenMail 2.1.13','image':IMAGE,
            'imap_tls_auth_read':'PASS','smtp_tls_auth':'PASS','reply_draft_headers':'PASS',
            'reviewed_send_local_copy':'PASS','recipient_received_exactly_once':'PASS',
            'wrong_pin_pre_secret':'PASS','wrong_password':'PASS',
            'model_calls':0,'external_mail_sent':False,'approval_source':'synthetic-test-presenter',
            'workflow_mode':'account-test','gateway_human_approval':'NOT_TESTED',
            'proton_authenticated_account':'NOT_TESTED'}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='hermes-greenmail-proof-') as temporary:
        with greenmail() as lab:
            result = test_provider(lab,Path(temporary)/'data')
    print(json.dumps(result,sort_keys=True,indent=2))


if __name__=='__main__':
    main()
