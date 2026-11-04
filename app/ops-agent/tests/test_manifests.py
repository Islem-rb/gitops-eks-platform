from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "gitops" / "apps" / "ops-agent" / "base"


def load(name):
    return [d for d in yaml.safe_load_all((BASE / name).read_text()) if d]


def rbac_rules():
    return [
        rule
        for doc in load("rbac.yaml")
        if doc["kind"] in ("Role", "ClusterRole")
        for rule in doc["rules"]
    ]


def test_rbac_is_read_only():
    for rule in rbac_rules():
        assert set(rule["verbs"]) <= {"get", "list"}


def test_rbac_cannot_read_secrets_configmaps_or_raw_logs():
    resources = {resource for rule in rbac_rules() for resource in rule["resources"]}
    assert not resources & {"secrets", "configmaps", "pods/log", "pods/exec", "*"}


def test_only_nodes_are_cluster_wide():
    cluster = [d for d in load("rbac.yaml") if d["kind"] == "ClusterRole"]
    assert [r["resources"] for d in cluster for r in d["rules"]] == [["nodes"]]


def test_kustomization_does_not_override_namespaces():
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    assert "namespace" not in kustomization
