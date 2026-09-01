# tests/test_scaffolding.py
import r2_upload_wizard


def test_package_has_version():
    assert r2_upload_wizard.__version__ == "0.1.0"
