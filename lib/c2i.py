"""C2I add-on profile request reconciliation. Python standard library only."""
import copy
import datetime as dt
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GROUP = 'c2i.dev/v1alpha1'
SENSITIVE = re.compile(r'password|secret|token|credential|api.?key|private.?key', re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('API redirects are disabled')


class API:
    def __init__(self, origin, headers, ca=None):
        self.origin, self.headers = origin.rstrip('/'), headers
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=ca)))

    def call(self, path, method='GET', body=None, merge=False):
        headers = dict(self.headers() if callable(self.headers) else self.headers)
        headers['Content-Type'] = 'application/merge-patch+json' if merge else 'application/json'
        req = urllib.request.Request(self.origin + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            # Upstream bodies can contain submitted values or credentials.
            raise RuntimeError(f'API returned HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError('API unavailable; outcome may be uncertain') from None


class Kubernetes(API):
    def __init__(self, namespace):
        sa = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        host = os.environ['KUBERNETES_SERVICE_HOST']
        if ':' in host:
            host = '[' + host + ']'
        super().__init__('https://' + host + ':' + os.getenv('KUBERNETES_SERVICE_PORT', '443'),
                         lambda: {'Authorization': 'Bearer ' + (sa / 'token').read_text().strip()},
                         str(sa / 'ca.crt'))
        self.namespace = namespace
        self.root = f'/apis/{GROUP}/namespaces/{namespace}'

    def items(self, plural):
        return self.call(f'{self.root}/{plural}')['items']

    def status(self, obj, status):
        # Optimistic concurrency: a stale request must never be submitted.
        return self.call(f"{self.root}/profilerequests/{obj['metadata']['name']}/status", 'PATCH',
                         {'metadata': {'resourceVersion': obj['metadata']['resourceVersion']},
                          'status': status}, merge=True)

    def card(self, obj, card):
        name = 'c2i-' + obj['metadata']['name']
        path = f'/api/v1/namespaces/{self.namespace}/configmaps'
        value = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
            'name': name, 'namespace': self.namespace, 'labels': {'c2i.dev/card': 'true'},
            'ownerReferences': [{'apiVersion': GROUP, 'kind': 'ProfileRequest',
                                 'name': obj['metadata']['name'], 'uid': obj['metadata']['uid']}]},
                 'data': {'card.json': json.dumps(card)}}
        # GET/create or replace only an owned ConfigMap. Never adopt unrelated data.
        try:
            previous = self.call(path + '/' + name)
        except RuntimeError as error:
            if str(error) != 'API returned HTTP 404':
                raise
            self.call(path, 'POST', value)
            return
        if not any(x.get('uid') == obj['metadata']['uid'] for x in previous['metadata'].get('ownerReferences', [])):
            raise RuntimeError('Card ConfigMap is owned by another resource')
        value['metadata']['resourceVersion'] = previous['metadata']['resourceVersion']
        if previous.get('data') != value['data']:
            self.call(path + '/' + name, 'PUT', value)


def pack_values(packs):
    return [{key: p[key] for key in ('name', 'tag', 'type', 'values', 'manifests') if key in p}
            for p in packs]


def attached(cluster, uid):
    return next((p for p in cluster['spec'].get('clusterProfileTemplates', []) if p['uid'] == uid), None)


def profile_variables(bindings, uid):
    binding = next((p for p in bindings if p['profileUid'] == uid), {})
    return {v['name']: str(v.get('value', v.get('defaultValue', ''))) for v in binding.get('variables', [])}


def base_state(cluster, bindings, uid):
    p = attached(cluster, uid)
    return {'packs': pack_values(p.get('packs', [])) if p else [],
            'variables': profile_variables(bindings, uid), 'attached': p is not None}


def make_plan(obj, profile, cluster, bindings, endpoint, project):
    spec = obj['spec']
    published = profile['spec']['published']
    if published.get('type', profile['spec'].get('type')) != 'add-on':
        raise ValueError('Only add-on profiles are supported')
    if published.get('profileVersion', profile['spec'].get('version')) != spec['profileVersion']:
        raise ValueError('Published profile version does not match the requested version')
    current = attached(cluster, spec['profileUid'])
    if current is None:
        raise ValueError('Attach the add-on profile to this cluster in VerteX first')
    definitions = {v['name']: v for v in profile['spec'].get('variables', published.get('variables', []))}
    variables = profile_variables(bindings, spec['profileUid'])
    for key, value in spec.get('settings', {}).items():
        definition = definitions.get(key)
        if not definition or definition.get('hidden') or definition.get('immutable') or definition.get('isSensitive') or SENSITIVE.search(key):
            raise ValueError('Settings must use editable, non-sensitive profile variables')
        variables[key] = value
    # Preserve existing deployment inputs; profile API validates format/regex constraints.
    payload = {'profiles': [{'uid': spec['profileUid'], 'packValues': pack_values(published['packs']),
                            'variables': [{'name': k, 'value': v} for k, v in sorted(variables.items())]}],
               'spcApplySettings': {'actionType': 'DownloadAndInstall'}}
    plan = {'requestUID': obj['metadata']['uid'], 'generation': obj['metadata']['generation'],
            'endpoint': endpoint, 'projectUid': project, 'clusterUid': spec['clusterUid'],
            'baseDigest': digest(base_state(cluster, bindings, spec['profileUid'])), 'payload': payload}
    return plan


def make_card(obj, status, profile=None, current_variables=None):
    spec = obj['spec']
    fields = []
    if profile:
        published = profile['spec']['published']
        for v in profile['spec'].get('variables', published.get('variables', [])):
            if v.get('hidden') or v.get('immutable') or v.get('isSensitive') or SENSITIVE.search(v['name']):
                continue
            fields.append({'name': v['name'], 'label': v.get('displayName', v['name']),
                           'format': v.get('format', 'string'), 'options': v.get('options', []),
                           'value': spec.get('settings', {}).get(v['name'], (current_variables or {}).get(v['name'], v.get('defaultValue', '')))})
    return {'schemaVersion': 1, 'name': obj['metadata']['name'], 'requestUID': obj['metadata']['uid'],
            'title': spec.get('displayName', obj['metadata']['name']), 'profileVersion': spec['profileVersion'],
            'phase': status.get('phase', 'Pending'), 'message': status.get('message', ''),
            'digest': status.get('planDigest', ''), 'settings': fields,
            'review': {'clusterUid': spec['clusterUid'], 'profileUid': spec['profileUid'],
                       'requestedSettings': {f['name']: f['value'] for f in fields if f['name'] in spec.get('settings', {})},
                       'baselineDigest': status.get('baseDigest', ''),
                       'profileContentDigest': status.get('profileContentDigest', '')},
            'vertexURL': status.get('vertexURL', ''),
            'actions': {'request': True, 'approve': status.get('phase') == 'PendingApproval'}}


def is_applied(status, cluster, bindings, uid):
    expected = status['expected']
    current = attached(cluster, uid)
    if not current or digest(pack_values(current.get('packs', []))) != expected['packsDigest']:
        return False
    actual = profile_variables(bindings, uid)
    if digest(actual) != expected['variablesDigest']:
        return False
    packs = current.get('packs', [])
    reported = cluster.get('status', {}).get('packs', [])
    if not packs or not all(any(r.get('name') == p['name'] and r.get('version') == p.get('tag') and
                               r.get('condition', {}).get('status') == 'True' for r in reported) for p in packs):
        return False
    try:
        applied = dt.datetime.fromisoformat(cluster['status']['spcApply']['patchAppliedTime'].replace('Z', '+00:00'))
        submitted = dt.datetime.fromisoformat(status['submittedAt'])
        return cluster['status'].get('state') == 'Running' and applied >= submitted
    except (KeyError, ValueError, TypeError):
        return False


class Controller:
    def __init__(self, kube, vertex, endpoint, project, allowed_clusters):
        self.kube, self.vertex, self.endpoint, self.project = kube, vertex, endpoint, project
        self.allowed_clusters = set(allowed_clusters)

    def reconcile(self, obj, approvals):
        spec, meta = obj['spec'], obj['metadata']
        status = copy.deepcopy(obj.get('status', {}))
        profile = None
        bindings = []
        try:
            if spec['clusterUid'] not in self.allowed_clusters:
                raise ValueError('Cluster is not in the operator allowlist')
            path = '/v1/spectroclusters/' + spec['clusterUid']
            cluster = self.vertex.call(path)
            bindings = self.vertex.call(path + '/variables')
            profile = copy.deepcopy(self.vertex.call('/v1/clusterprofiles/' + spec['profileUid']))
            profile['spec']['variables'] = self.vertex.call('/v1/clusterprofiles/' + spec['profileUid'] + '/variables')['variables']
            # Never repeat a mutation after a crash or a timeout. Read-only verification continues.
            if status.get('phase') in ('Submitting', 'Applying', 'SubmissionUncertain', 'Ready', 'Drifted'):
                if status.get('observedGeneration') != meta['generation']:
                    raise ValueError('Request spec changed after submission; create a new request')
                if is_applied(status, cluster, bindings, spec['profileUid']):
                    status.update(phase='Ready', message='Profile packs and variables confirmed by VerteX')
                elif status['phase'] == 'Submitting':
                    status.update(phase='SubmissionUncertain', message='Submission interrupted; review in VerteX before retrying')
                elif status['phase'] in ('Ready', 'Drifted'):
                    status.update(phase='Drifted', message='Previously confirmed configuration has drifted; no automatic reapply')
            else:
                plan = make_plan(obj, profile, cluster, bindings, self.endpoint, self.project)
                sha = digest(plan)
                status = {'phase': 'PendingApproval', 'planDigest': sha, 'baseDigest': plan['baseDigest'],
                          'profileContentDigest': digest(plan['payload']['profiles'][0]['packValues']),
                          'observedGeneration': meta['generation'],
                          'vertexURL': self.endpoint + '/projects/' + self.project + '/clusters/' + spec['clusterUid'] + '/overview',
                          'message': 'Review settings and profile content, then approve this digest'}
                matches = [a for a in approvals if a['spec']['requestUID'] == meta['uid'] and a['spec']['planDigest'] == sha]
                if any(a['spec']['decision'] == 'Reject' for a in matches):
                    status.update(phase='Rejected', message='Reviewer rejected this plan')
                elif any(a['spec']['decision'] == 'Approve' for a in matches):
                    payload = plan['payload']
                    desired = payload['profiles'][0]
                    status.update(phase='Submitting', submittedAt=now(), approvalNames=[a['metadata']['name'] for a in matches],
                                  expected={'packsDigest': digest(desired['packValues']),
                                            'variablesDigest': digest({v['name']: v['value'] for v in desired['variables']})})
                    # Durable write BEFORE mutation; resourceVersion conflict stops submission.
                    obj = self.kube.status(obj, status)
                    try:
                        self.vertex.call(path + '/profiles?resolveNotification=true', 'PATCH', payload)
                        self.vertex.call(path + '/status/spcApply', 'POST', {})
                        status.update(phase='Applying', message='Approved update submitted; waiting for cluster confirmation')
                    except Exception:
                        status.update(phase='SubmissionUncertain', message='Submission outcome uncertain; review in VerteX; never automatically retried')
        except ValueError as error:
            status.update(message=str(error))
            if status.get('phase') not in ('Submitting', 'Applying', 'SubmissionUncertain', 'Ready', 'Drifted'):
                status['phase'] = 'Blocked'
        except Exception:
            # Preserve the pending digest and in-flight phase on transient read failures.
            status['message'] = 'Reconciliation unavailable; check operator access and API connectivity'
            status.setdefault('phase', 'Blocked')
        if status != obj.get('status', {}):
            obj = self.kube.status(obj, status)
        self.kube.card(obj, make_card(obj, status, profile, profile_variables(bindings, spec['profileUid'])))
        return status


def main():
    namespace = os.environ['C2I_NAMESPACE']
    endpoint = os.environ['VERTEX_ENDPOINT'].rstrip('/')
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError('VERTEX_ENDPOINT must be an HTTPS origin')
    project = os.environ['VERTEX_PROJECT_UID']
    if not re.fullmatch('[a-f0-9]{24}', project):
        raise ValueError('Configure a valid project UID')
    vertex = API(endpoint, {'Apikey': Path('/credentials/api-key').read_text().strip(), 'ProjectUid': project})
    kube = Kubernetes(namespace)
    controller = Controller(kube, vertex, endpoint, project, os.environ['C2I_ALLOWED_CLUSTERS'].split(','))
    approvals = kube.items('profileapprovals')
    failures = 0
    requests = kube.items('profilerequests')
    active = {}
    for request in requests:
        if request.get('status', {}).get('phase') in ('Submitting', 'Applying', 'SubmissionUncertain'):
            active.setdefault(request['spec']['clusterUid'], set()).add(request['metadata']['uid'])
    for obj in requests:
        if obj['metadata'].get('deletionTimestamp'):
            continue
        uid, cluster = obj['metadata']['uid'], obj['spec']['clusterUid']
        if active.get(cluster) and uid not in active[cluster]:
            continue
        try:
            status = controller.reconcile(obj, approvals)
            if status['phase'] in ('Submitting', 'Applying', 'SubmissionUncertain'):
                active.setdefault(cluster, set()).add(uid)
        except Exception:
            failures += 1
            print(json.dumps({'level': 'error', 'msg': 'Request reconciliation failed', 'request': obj['metadata']['name']}))
    if failures:
        raise RuntimeError('Some requests could not be reconciled')
