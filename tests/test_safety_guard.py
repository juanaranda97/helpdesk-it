"""Verifica que el bloqueo de seguridad de la suite rechaza cualquier base que no
sea el entorno local autorizado (nunca Supabase productivo)."""
import pytest
from tests.conftest import _assert_safe_test_db


def test_rechaza_supabase_productivo():
    with pytest.raises(RuntimeError):
        _assert_safe_test_db("postgresql://postgres:x@db.ctazrasxhkgckcbenhvb.supabase.co:5432/postgres")


def test_rechaza_host_remoto():
    with pytest.raises(RuntimeError):
        _assert_safe_test_db("postgresql://u:p@10.20.30.40:5432/postgres")


def test_rechaza_nombre_no_de_pruebas_en_host_local():
    with pytest.raises(RuntimeError):
        _assert_safe_test_db("postgresql://postgres@127.0.0.1:5432/postgres")


def test_acepta_base_local_de_pruebas():
    u = _assert_safe_test_db("postgresql://postgres@127.0.0.1:5433/coretest")
    assert u.database == "coretest"

