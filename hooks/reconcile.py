#!/usr/bin/env python3
import json
import os
import sys
sys.path.insert(0, '/opt/c2i')
if '--config' in sys.argv:
    namespace = os.environ.get('C2I_NAMESPACE', 'c2i-system')
    print(json.dumps({'configVersion': 'v1', 'kubernetes': [
        {'name': resource, 'apiVersion': 'c2i.dev/v1alpha1', 'kind': kind,
         'namespace': {'nameSelector': {'matchNames': [namespace]}},
         'executeHookOnEvent': ['Added', 'Modified', 'Deleted'],
         'jqFilter': '{spec: .spec, uid: .metadata.uid, generation: .metadata.generation}'}
        for resource, kind in [('requests', 'ProfileRequest'), ('approvals', 'ProfileApproval')]],
        'schedule': [{'name': 'poll', 'crontab': '*/30 * * * * *'}]}))
else:
    from c2i import main
    main()
