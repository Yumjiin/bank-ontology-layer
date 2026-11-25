from pathlib import Path

import pytest
import yaml

from metadata import Ontology, OntologyError, load_ontology

ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "ontology" / "ontology.yaml"
if not ONTOLOGY_PATH.exists():  # 컨테이너 안에서는 /app/ontology에 마운트됨
    ONTOLOGY_PATH = Path("/app/ontology/ontology.yaml")


def test_ontology_yaml_is_valid():
    ontology = load_ontology(ONTOLOGY_PATH)
    assert "Customer" in ontology.object_types
    assert ontology.reference_errors() == []


def test_broken_link_reference_is_detected(tmp_path):
    raw = yaml.safe_load(ONTOLOGY_PATH.read_text(encoding="utf-8"))
    raw["link_types"]["owns"]["to"] = "NoSuchObject"
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(OntologyError):
        load_ontology(broken)


def test_metric_path_must_follow_links():
    raw = yaml.safe_load(ONTOLOGY_PATH.read_text(encoding="utf-8"))
    raw["metrics"]["active_customers"]["path"] = ["has_transaction"]  # Customer에서 시작할 수 없는 링크
    errors = Ontology.model_validate(raw).reference_errors()
    assert any("active_customers" in e for e in errors)


def _broken(mutate):
    raw = yaml.safe_load(ONTOLOGY_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    return Ontology.model_validate(raw).reference_errors()


def test_via_condition_must_be_simple_equality():
    # 임의 SQL을 막기 위해 "컬럼 = '값'" 형식만 허용함
    def mutate(raw):
        raw["link_types"]["owns"]["via"]["condition"] = "rel_cd = '01' OR 1=1"
    assert any("via.condition" in e for e in _broken(mutate))


def test_metric_period_must_be_date():
    def mutate(raw):
        raw["metrics"]["net_deposit"]["period"] = "Transaction.amount"
    assert any("date 타입" in e for e in _broken(mutate))


def test_metric_property_must_be_on_path():
    def mutate(raw):
        raw["metrics"]["net_deposit"]["measure"]["property"] = "Customer.cust_no"
    assert any("지표 경로" in e for e in _broken(mutate))
