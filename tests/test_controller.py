import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from c2i import Controller, digest, make_card, make_plan

ENDPOINT = 'https://vertex.example.test'
PROJECT, CLUSTER, PROFILE = 'a'*24, 'b'*24, 'c'*24
PACK = {'name': 'app', 'type': 'helm', 'tag': '1.0.0', 'values': 'replicas: "{{.spectro.var.application_replicas}}"'}


def fixture():
    obj = {'apiVersion': 'c2i.dev/v1alpha1', 'kind': 'ProfileRequest',
           'metadata': {'name': 'app-001', 'uid': 'request-uid', 'generation': 1, 'resourceVersion': '1'},
           'spec': {'clusterUid': CLUSTER, 'profileUid': PROFILE, 'profileVersion': '1.0.0',
                    'settings': {'application_replicas': '3'}}}
    profile = {'spec': {'version': '1.0.0', 'published': {'type': 'add-on', 'profileVersion': '1.0.0', 'version': 7, 'packs': [copy.deepcopy(PACK)]},
                        'variables': [{'name': 'application_replicas', 'format': 'number'},
                                      {'name': 'access_value', 'isSensitive': True, 'defaultValue': 'must-not-appear'}]}}
    cluster = {'spec': {'clusterProfileTemplates': [{'uid': PROFILE, 'packs': [copy.deepcopy(PACK)]}]},
               'status': {'state': 'Running', 'packs': [{'name': 'app', 'version': '1.0.0', 'condition': {'status': 'True'}}]}}
    bindings = [{'profileUid': PROFILE, 'variables': [{'name': 'application_replicas', 'value': '1'},
                                                     {'name': 'access_value', 'value': 'must-not-appear'}]}]
    return obj, profile, cluster, bindings


class Kube:
    def __init__(self): self.writes=[]; self.cards=[]; self.fail=False
    def status(self, obj, status):
        if self.fail: raise RuntimeError('conflict')
        self.writes.append(copy.deepcopy(status))
        result=copy.deepcopy(obj);result['status']=copy.deepcopy(status)
        result['metadata']['resourceVersion']=str(int(obj['metadata']['resourceVersion'])+1)
        return result
    def card(self, obj, card): self.cards.append(card)


class Vertex:
    def __init__(self, profile, cluster, bindings):
        self.profile, self.cluster, self.bindings = profile, cluster, bindings
        self.writes=[];self.fail_write=False
    def call(self, path, method='GET', body=None):
        if method!='GET':
            self.writes.append((path,method,copy.deepcopy(body)))
            if self.fail_write: raise RuntimeError('timeout')
            return {}
        if path.startswith('/v1/clusterprofiles/'):
            if path.endswith('/variables'): return {'variables': self.profile['spec']['variables']}
            result=copy.deepcopy(self.profile);result['spec'].pop('variables', None)
            return result
        if path.endswith('/variables'): return self.bindings
        return self.cluster


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.obj,self.profile,self.cluster,self.bindings=fixture()
        self.k=Kube();self.v=Vertex(self.profile,self.cluster,self.bindings)
        self.c=Controller(self.k,self.v,ENDPOINT,PROJECT,[CLUSTER])
    def approve(self, sha=None, uid=None):
        plan=make_plan(self.obj,self.profile,self.cluster,self.bindings,ENDPOINT,PROJECT)
        return [{'metadata': {'name':'review'},'spec':{'requestUID':uid or 'request-uid','planDigest':sha or digest(plan),'decision':'Approve'}}]
    def test_no_mutation_before_approval(self):
        s=self.c.reconcile(self.obj,[])
        self.assertEqual(s['phase'],'PendingApproval');self.assertEqual(self.v.writes,[])
    def test_exact_approval_and_durable_submission(self):
        s=self.c.reconcile(self.obj,self.approve())
        self.assertEqual(s['phase'],'Applying')
        self.assertEqual(self.k.writes[0]['phase'],'Submitting')
        self.assertEqual([x[1] for x in self.v.writes],['PATCH','POST'])
        self.assertNotIn('must-not-appear',json.dumps(self.k.writes))
        self.assertNotIn('must-not-appear',json.dumps(self.k.cards))
    def test_changed_content_invalidates_approval(self):
        approval=self.approve();self.profile['spec']['published']['packs'][0]['values']='replicas: 99'
        self.assertEqual(self.c.reconcile(self.obj,approval)['phase'],'PendingApproval')
        self.assertEqual(self.v.writes,[])
    def test_drift_invalidates_approval(self):
        approval=self.approve();self.bindings[0]['variables'][0]['value']='2'
        self.c.reconcile(self.obj,approval);self.assertEqual(self.v.writes,[])
    def test_approval_cannot_replay_for_recreated_request(self):
        self.c.reconcile(self.obj,self.approve(uid='old-request-uid'));self.assertEqual(self.v.writes,[])
    def test_status_conflict_prevents_mutation(self):
        self.k.fail=True
        with self.assertRaises(RuntimeError):self.c.reconcile(self.obj,self.approve())
        self.assertEqual(self.v.writes,[])
    def test_timeout_is_never_replayed(self):
        self.v.fail_write=True;s=self.c.reconcile(self.obj,self.approve())
        self.assertEqual(s['phase'],'SubmissionUncertain');self.obj['status']=s
        self.v.fail_write=False;self.c.reconcile(self.obj,self.approve())
        self.assertEqual(len(self.v.writes),1)
    def test_crash_after_submission_marker_is_never_replayed(self):
        self.c.reconcile(self.obj,self.approve());self.obj['status']=self.k.writes[0]
        self.v.writes.clear()
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'SubmissionUncertain')
        self.assertEqual(self.v.writes,[])
    def test_ready_requires_revision_variables_and_fresh_apply_confirmation(self):
        s=self.c.reconcile(self.obj,self.approve());self.obj['status']=s
        self.cluster['status']['spcApply']={'patchAppliedTime':'2000-01-01T00:00:00Z'}
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Applying')
        self.bindings[0]['variables'][0]['value']='3'
        self.cluster['status']['spcApply']['patchAppliedTime']='2099-01-01T00:00:00Z'
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Ready')
    def test_sensitive_settings_blocked(self):
        self.obj['spec']['settings']={'access_value':'something'}
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Blocked');self.assertEqual(self.v.writes,[])
    def test_infra_and_unattached_profile_blocked(self):
        self.profile['spec']['published']['type']='infra'
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Blocked')
        self.profile['spec']['published']['type']='add-on';self.cluster['spec']['clusterProfileTemplates']=[]
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Blocked')
    def test_cluster_allowlist(self):
        self.c.allowed_clusters=set()
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Blocked');self.assertEqual(self.v.writes,[])
    def test_existing_deployment_inputs_preserved(self):
        self.c.reconcile(self.obj,self.approve())
        values=self.v.writes[0][2]['profiles'][0]['variables']
        self.assertEqual({v['name']:v['value'] for v in values},
                         {'application_replicas':'3','access_value':'must-not-appear'})
    def test_profile_version_mismatch_blocked(self):
        self.obj['spec']['profileVersion']='2.0.0'
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Blocked')
        self.assertEqual(self.v.writes,[])
    def test_ready_drift_does_not_reapply(self):
        s=self.c.reconcile(self.obj,self.approve());self.obj['status']=s
        self.bindings[0]['variables'][0]['value']='3'
        self.cluster['status']['spcApply']={'patchAppliedTime':'2099-01-01T00:00:00Z'}
        self.obj['status']=self.c.reconcile(self.obj,[])
        self.bindings[0]['variables'][0]['value']='4'
        self.assertEqual(self.c.reconcile(self.obj,[])['phase'],'Drifted')
        self.assertEqual(len(self.v.writes),2)
    def test_rejection_wins_over_approval(self):
        approvals=self.approve();rejection=copy.deepcopy(approvals[0]);rejection['spec']['decision']='Reject'
        self.assertEqual(self.c.reconcile(self.obj,approvals+[rejection])['phase'],'Rejected')
        self.assertEqual(self.v.writes,[])

if __name__=='__main__':unittest.main()
