#!/usr/bin/env bash
set -euo pipefail
# Only a disposable, explicitly named local kind cluster is allowed.
[ "$(kubectl config current-context)" = kind-c2i-test ] || { echo 'Expected disposable context kind-c2i-test'; exit 1; }
kubectl apply -f deploy/crds.yaml
kubectl wait --for=condition=Established --timeout=60s crd/profilerequests.c2i.dev crd/profileapprovals.c2i.dev
kubectl create namespace c2i-system
kubectl -n c2i-system create secret generic c2i-vertex --from-literal=api-key=fixture-only-not-a-real-key
sed -e 's/REPLACE_WITH_PROJECT_UID/aaaaaaaaaaaaaaaaaaaaaaaa/g' \
    -e 's/REPLACE_WITH_CLUSTER_UID/bbbbbbbbbbbbbbbbbbbbbbbb/g' deploy/operator.yaml | kubectl apply -f -
kubectl -n c2i-system rollout status deployment/c2i-operator --timeout=120s
# This cluster is outside the allowlist: no external API should be called.
cat <<'JSON' | kubectl create -f -
{"apiVersion":"c2i.dev/v1alpha1","kind":"ProfileRequest","metadata":{"name":"smoke","namespace":"c2i-system"},"spec":{"clusterUid":"dddddddddddddddddddddddd","profileUid":"cccccccccccccccccccccccc","profileVersion":"1.0.0","settings":{}}}
JSON
kubectl -n c2i-system wait --for=jsonpath='{.status.phase}'=Blocked profilerequest/smoke --timeout=90s
kubectl -n c2i-system wait --for=create configmap/c2i-smoke --timeout=60s
kubectl -n c2i-system get configmap c2i-smoke -o json | jq -e '.data["card.json"] | fromjson | .phase == "Blocked"'
if kubectl -n c2i-system patch profilerequest smoke --type=merge -p '{"spec":{"profileVersion":"2.0.0"}}'; then
  echo 'Immutable request was incorrectly accepted'; exit 1
fi
kubectl -n c2i-system create serviceaccount test-requester
kubectl -n c2i-system create rolebinding test-requester --role=c2i-requester --serviceaccount=c2i-system:test-requester
[ "$(kubectl -n c2i-system auth can-i create profileapprovals.c2i.dev --as=system:serviceaccount:c2i-system:test-requester)" = no ]
[ "$(kubectl -n c2i-system auth can-i patch profilerequests.c2i.dev --subresource=status --as=system:serviceaccount:c2i-system:test-requester)" = no ]
echo 'Kubernetes reconciliation, cards, immutability and RBAC smoke test passed'
