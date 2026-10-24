"""Explicit loopback connection settings for a local or SSH-forwarded native host.

Loading settings never connects, starts Android, or relaxes the version gate.
The pinned minimal protocol has no live identity attestation command: a status
response and an Android identity receipt are different kinds of evidence.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import math
import os
import re

from .runtime import local
from .versioning import VersionMismatch, canonical_hash

SCHEMA = 'royale-native-connection.v1'
ENVIRONMENT_VARIABLE = 'ROYALE_NATIVE_CONNECTION'


@dataclass(frozen=True)
class NativeConnection:
    host: str = '127.0.0.1'
    port: int = 26789
    timeout: float = 60
    version_manifest: str = 'data/phase2/version_manifest.json'
    identity_path: str | None = None
    manifest_sha256: str | None = None
    configuration_path: str | None = None

    def __post_init__(self):
        host = '127.0.0.1' if self.host == 'localhost' else self.host
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            loopback = False
        if not loopback:
            raise ValueError('Native connection must use loopback; use an authenticated SSH local forward')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('Invalid native connection port')
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or not 0 < self.timeout <= 600):
            raise ValueError('Native connection timeout must be finite and at most 600 seconds')
        for path in (self.version_manifest, self.identity_path):
            if path is not None:
                if not isinstance(path, str) or not path:
                    raise ValueError('Native evidence paths must be nonempty strings')
                local(path)
        digest = self.manifest_sha256
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('manifest_sha256 must be a lower-case SHA256 digest')
        object.__setattr__(self, 'host', host)

    def check_manifest_lock(self):
        data = local(self.version_manifest).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if self.manifest_sha256 is not None and digest != self.manifest_sha256:
            raise VersionMismatch('Connection manifest SHA256 differs from the configured lock')
        return digest

    def as_dict(self):
        result = {'schema': SCHEMA, 'transport': 'tcp-loopback', 'host': self.host,
                  'port': self.port, 'timeout': self.timeout,
                  'version_manifest': self.version_manifest, 'identity_path': self.identity_path,
                  'manifest_sha256': self.manifest_sha256}
        return result


def load_connection(path=None):
    """Return explicit settings, or None for the preserved legacy defaults."""
    path = path or os.environ.get(ENVIRONMENT_VARIABLE)
    if not path:
        return None
    config_path = local(path)
    data = json.loads(config_path.read_text(encoding='utf-8-sig'))
    allowed = {'schema', 'transport', 'host', 'port', 'timeout', 'version_manifest',
               'identity_path', 'manifest_sha256'}
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError('Unknown native connection schema')
    if set(data) - allowed:
        raise ValueError('Unexpected native connection keys: ' + ', '.join(sorted(set(data) - allowed)))
    if data.get('transport') != 'tcp-loopback':
        raise ValueError('Only the preserved loopback TCP probe protocol is supported')
    options = {key: value for key, value in data.items() if key not in ('schema', 'transport')}
    settings = NativeConnection(**options, configuration_path=str(config_path))
    settings.check_manifest_lock()
    return settings


def check_connection(path=None, *, connect=False):
    """Check local files; contact only this configured endpoint when requested.

    Even a healthy transport is not a native battle acceptance. No configure,
    step, reset, schedule, renderer, VM, or fallback is started here.
    """
    from .probe import ProbeClient
    from .versioning import verify_manifest
    report = {'schema': 'royale-native-connection-check.v1',
              'evidence_scope': 'native_prerequisites', 'rpc_calls': 0,
              'connection_attempted': False, 'reachable': None,
              'native_runtime_verified': False, 'native_battle_execution_verified': False,
              'fallback_used': False, 'status': 'unavailable', 'checks': {},
              'limitations': ['The minimal status protocol does not attest remote binary/content identity.',
                             'A recorded Android identity receipt must be renewed after runtime changes.',
                             'Only reset/play/consumption/terminal acceptance can verify battle execution.']}
    try:
        settings = load_connection(path) or NativeConnection()
        report['connection'] = settings.as_dict()
        report['manifest_sha256'] = settings.check_manifest_lock()
        manifest = json.loads(local(settings.version_manifest).read_text(encoding='utf-8-sig'))
        if not isinstance(manifest, dict):
            raise VersionMismatch('Version manifest must be an object')
        report['version'] = {key: manifest.get(key) for key in
                             ('schema', 'runtime_kind', 'client_build', 'content_version', 'abi', 'ticks_per_second')}
        report['hashrefs'] = [{'path': settings.version_manifest, 'sha256': report['manifest_sha256']}]
        missing = [a['path'] for a in manifest.get('assets', []) if not local(a['path']).is_file()]
        report['checks']['local_assets'] = {'missing_count': len(missing), 'missing': missing[:25]}
        try:
            version = verify_manifest(settings.version_manifest)
            if version['manifest']['ticks_per_second'] != 20:
                raise VersionMismatch('This adapter supports only the measured 20 Hz native protocol')
            report['checks']['version'] = {'status': 'pass', 'fingerprint': version['fingerprint']}
            report['version_fingerprint'] = version['fingerprint']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report['checks']['version'] = {'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}'}
        identity_path = settings.identity_path or manifest.get('runtime_identity_receipt')
        if not identity_path:
            report['checks']['identity'] = {'status': 'blocked', 'error': 'No Android identity receipt configured'}
        else:
            try:
                identity, digest = verify_runtime_identity(manifest, identity_path)
                report['hashrefs'].append({'path': str(local(identity_path)), 'sha256': digest})
                report['checks']['identity'] = {'status': 'pass', 'path': str(local(identity_path)),
                    'sha256': digest, 'pid': identity.get('pid'), 'created_utc': identity.get('created_utc'),
                    'current_process_identity_verified': False}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report['checks']['identity'] = {'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}'}
        if connect:
            report['connection_attempted'] = True
            client = ProbeClient(host=settings.host, port=settings.port, timeout=settings.timeout)
            try:
                report['rpc_calls'] = 1
                status = client.status()
                if not isinstance(status, dict) or status.get('ok') is not True:
                    raise ValueError('Native status must be an object with ok=true')
                report.update(reachable=True, native_status=status)
                report['checks']['transport'] = {'status': 'pass'}
                report['checks']['content'] = {'status': 'pass' if status.get('contentReady') is True else 'blocked'}
            except (OSError, ValueError, RuntimeError) as exc:
                report['reachable'] = False
                report['checks']['transport'] = {'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}'}
            finally:
                client.close()
        else:
            report['checks']['transport'] = {'status': 'not_run', 'reason': 'Use --connect explicitly'}
        if all(report['checks'][key]['status'] == 'pass' for key in ('version', 'identity')):
            report['status'] = 'ready_assets'
            if connect and not (report['reachable'] and report['checks'].get('content', {}).get('status') == 'pass'):
                report['status'] = 'unavailable'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    report['ready'] = report['status'] == 'ready_assets'
    return report


def verify_runtime_identity(manifest, receipt_path):
    """Validate the historical receipt gate without treating it as live attest."""
    raw = local(receipt_path).read_bytes()
    identity = json.loads(raw)
    if not isinstance(identity, dict):
        raise VersionMismatch('Android identity receipt must be an object')
    if (identity.get('schema') != 'phase2-native-adb-identity.v1'
            or not all(identity.get(key) is True for key in
                       ('actual_adb_verified', 'loaded_libg_verified', 'content_verified'))):
        raise VersionMismatch('Runtime component identity has not been actually verified')
    libraries = {a['sha256'] for a in manifest['assets'] if a.get('role') == 'native_library'}
    probes = {a['sha256'] for a in manifest['assets'] if a.get('role') == 'native_probe'}
    fingerprints = {a['sha256'] for a in manifest['assets'] if a.get('role') == 'content_fingerprint'}
    expected = {'libg_sha256': libraries, 'probe_sha256': probes,
                'content_fingerprint_sha256': fingerprints}
    for field, allowed in expected.items():
        if identity.get(field) not in allowed:
            raise VersionMismatch('Runtime identity differs from the manifest: ' + field)
    for field, expected_value in (
            ('content_manifest_sha1', manifest.get('content_manifest_sha1')),
            ('apk_sha256', manifest.get('offline_apk_sha256')),
            ('content_version', manifest.get('content_version', manifest.get('runtime_content_version')))):
        if not expected_value or identity.get(field) != expected_value:
            raise VersionMismatch('Runtime identity differs from the manifest: ' + field)
    if manifest.get('profile') == 'separate_local_build_runtime_identity':
        _verify_local_build_identity_evidence(manifest, identity)
    return identity, hashlib.sha256(raw).hexdigest()


def _verify_local_build_identity_evidence(manifest, identity):
    """Bind the new profile's recorded identity to its complete ADB file evidence.

    This checks a saved capture, not a live RPC process or device attestation.
    The collector helpers below only generate/parse text; no ADB is executed.
    Legacy profiles retain their historical receipt format.
    """
    from tools.cloud.native_identity import (CONTENT_PREFIX, PACKAGE, SERIAL,
        discovery_script, inventory, parse_hashes, verification_script)

    fingerprint = canonical_hash({k: v for k, v in manifest.items() if k != 'fingerprint'})
    if (identity.get('version_fingerprint') != fingerprint
            or manifest.get('fingerprint', fingerprint) != fingerprint):
        raise VersionMismatch('Runtime identity version fingerprint differs from the manifest')
    for field in ('pid', 'process_start_ticks'):
        if type(identity.get(field)) is not int or identity[field] <= 0:
            raise VersionMismatch('Runtime identity requires a positive process ' + field)
    if not isinstance(identity.get('device_serial'), str) or not SERIAL.fullmatch(identity['device_serial']):
        raise VersionMismatch('Runtime identity requires a valid explicit device serial')
    if not isinstance(manifest.get('client_package'), str) or not PACKAGE.fullmatch(manifest['client_package']):
        raise VersionMismatch('Runtime identity requires a strict Android package name')

    references = identity.get('source_evidence')
    if not isinstance(references, list) or not references:
        raise VersionMismatch('Runtime identity requires source evidence')
    files = {}
    manifest_found = False
    try:
        for row in references:
            if (not isinstance(row, dict) or not isinstance(row.get('path'), str) or not row['path']
                    or not isinstance(row.get('sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])):
                raise ValueError('Malformed source evidence reference')
            path = local(row['path'])
            if path in files:
                raise ValueError('Duplicate source evidence path')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('Source evidence SHA256 mismatch: ' + row['path'])
            if not raw.strip() and not path.name.endswith('.stderr.log'):
                raise ValueError('Source evidence is empty: ' + row['path'])
            files[path] = raw
            if path.suffix == '.json':
                try:
                    manifest_found |= json.loads(raw) == manifest
                except (ValueError, UnicodeError):
                    pass
        if not manifest_found:
            raise ValueError('Source evidence does not contain the current manifest')

        captured = {}
        for label in ('discover-runtime', 'verify-installed-files'):
            scripts = [p for p in files if re.fullmatch(r'\d+-' + label + r'\.stdin\.sh', p.name)]
            if len(scripts) != 1:
                raise ValueError('Exactly one source script is required: ' + label)
            script = scripts[0]
            prefix = script.name.removesuffix('.stdin.sh')
            stdout, stderr = (script.with_name(prefix + suffix) for suffix in ('.stdout.log', '.stderr.log'))
            if stdout not in files or stderr not in files:
                raise ValueError('Source script stdout/stderr evidence is missing: ' + label)
            captured[label] = (files[script].decode('utf-8'),
                               files[stdout].decode('utf-8').replace('\r\n', '\n'))
        package = manifest['client_package']
        discovery, first = captured['discover-runtime']
        if discovery != discovery_script(package):
            raise ValueError('Discovery script differs from the read-only collector')
        found = inventory(first, package)
        if (found['pid'] != identity['pid'] or found['start_ticks'] != identity['process_start_ticks']
                or identity.get('mapped_paths') != {key: found[key] for key in ('apk', 'libg', 'probe', 'update_root')}):
            raise ValueError('Receipt process or mapped paths differ from discovery evidence')
        remote = {}
        for key, role in (('apk', 'offline_apk'), ('libg', 'native_library'), ('probe', 'native_probe')):
            selected = [r for r in manifest['assets'] if r.get('role') == role]
            if len(selected) != 1:
                raise ValueError('Exactly one manifest asset is required: ' + role)
            remote[found[key]] = selected[0]
        update = [r for r in manifest['assets'] if r.get('role') in ('paired_content', 'content_fingerprint')]
        if len(update) != 652 or len({r['path'] for r in update}) != 652:
            raise ValueError('Exactly 652 distinct installed update resources are required')
        for row in update:
            name = row['path'].removeprefix(CONTENT_PREFIX)
            if (not row['path'].startswith(CONTENT_PREFIX) or not name
                    or '..' in name.split('/') or name.startswith('/')
                    or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)):
                raise ValueError('Unsafe installed update resource path')
            remote[found['update_root'] + '/' + name] = row
        verification, verified = captured['verify-installed-files']
        if verification != verification_script(package, found, list(remote)):
            raise ValueError('Verification script differs from the complete read-only collector')
        after = inventory(verified, package)
        if any(after[key] != found[key] for key in ('pid', 'start_ticks', 'apk', 'libg', 'probe', 'update_root')):
            raise ValueError('Process identity or loaded paths changed during evidence capture')
        hashes, sizes = parse_hashes(verified, remote)
        if any(hashes[path] != row['sha256'] or sizes[path] != row['bytes'] for path, row in remote.items()):
            raise ValueError('Installed resource hashes or byte sizes differ from the manifest')
        if (identity.get('installed_update_file_count') != 652
                or identity.get('installed_update_bytes') != sum(row['bytes'] for row in update)):
            raise ValueError('Installed update totals differ from the manifest')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VersionMismatch('Runtime identity source evidence is invalid: ' + str(exc)) from exc
