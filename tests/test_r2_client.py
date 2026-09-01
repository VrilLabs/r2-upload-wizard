from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from r2_upload_wizard import r2_client


def _stubbed_client():
    client = boto3.client(
        "s3",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        aws_access_key_id="key",
        aws_secret_access_key="secret",
        region_name="auto",
    )
    return client, Stubber(client)


def test_build_client_sets_checksum_config():
    client = r2_client.build_client(
        account_id="a" * 32,
        access_key_id="key",
        secret_access_key="secret",
        s3_url="https://a" * 4 + ".r2.cloudflarestorage.com",
    )
    checksum_context = client.meta.config.request_checksum_calculation
    assert checksum_context == "when_required"
    assert client.meta.config.response_checksum_validation == "when_required"


def test_list_buckets_maps_to_bucket_info():
    client, stubber = _stubbed_client()
    created = datetime(2026, 1, 1, tzinfo=UTC)
    stubber.add_response(
        "list_buckets",
        {"Buckets": [{"Name": "photos", "CreationDate": created}], "Owner": {}},
    )
    with stubber:
        buckets = r2_client.list_buckets(client)
    assert buckets == [r2_client.BucketInfo(name="photos", creation_date=created)]


def test_head_object_size_found():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "head_object",
        {"ContentLength": 42},
        expected_params={"Bucket": "b", "Key": "k"},
    )
    with stubber:
        assert r2_client.head_object_size(client, "b", "k") == 42


def test_head_object_size_missing_returns_none():
    client, stubber = _stubbed_client()
    stubber.add_client_error("head_object", service_error_code="404")
    with stubber:
        assert r2_client.head_object_size(client, "b", "missing-key") is None


def test_head_object_size_reraises_other_errors():
    client, stubber = _stubbed_client()
    stubber.add_client_error("head_object", service_error_code="AccessDenied")
    with stubber, __import__("pytest").raises(ClientError):
        r2_client.head_object_size(client, "b", "k")


def test_validate_bucket_name_rules():
    assert r2_client.validate_bucket_name("valid-name") is None
    assert r2_client.validate_bucket_name("ab") is not None  # too short
    assert r2_client.validate_bucket_name("Has-Upper") is not None
    assert r2_client.validate_bucket_name("-leading-hyphen") is not None
    assert r2_client.validate_bucket_name("trailing-hyphen-") is not None


def test_is_bucket_empty_true():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 0, "IsTruncated": False, "Contents": []},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber:
        empty, approx = r2_client.is_bucket_empty(client, "b")
    assert empty is True
    assert approx == "0"


def test_is_bucket_empty_false_with_exact_count():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1, "IsTruncated": False, "Contents": [{"Key": "x"}]},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber:
        empty, approx = r2_client.is_bucket_empty(client, "b")
    assert empty is False
    assert approx == "1"


def test_is_bucket_empty_false_truncated_shows_plus():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1, "IsTruncated": True, "Contents": [{"Key": "x"}]},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber:
        empty, approx = r2_client.is_bucket_empty(client, "b")
    assert empty is False
    assert approx == "1+"


def test_create_bucket_rejects_invalid_name_without_a_call():
    client, stubber = _stubbed_client()
    with stubber, __import__("pytest").raises(ValueError):
        r2_client.create_bucket(client, "AB")


def test_create_bucket_success():
    client, stubber = _stubbed_client()
    stubber.add_response("create_bucket", {}, expected_params={"Bucket": "new-bucket"})
    with stubber:
        r2_client.create_bucket(client, "new-bucket")


def test_create_bucket_already_exists_maps_to_typed_error():
    client, stubber = _stubbed_client()
    stubber.add_client_error("create_bucket", service_error_code="BucketAlreadyExists")
    with stubber, __import__("pytest").raises(r2_client.BucketAlreadyExistsError):
        r2_client.create_bucket(client, "taken")


def test_delete_bucket_refuses_when_not_empty():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1, "IsTruncated": False, "Contents": [{"Key": "x"}]},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber, __import__("pytest").raises(r2_client.BucketNotEmptyError) as excinfo:
        r2_client.delete_bucket(client, "b")
    assert excinfo.value.approx_count == "1"


def test_delete_bucket_succeeds_when_empty():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 0, "IsTruncated": False, "Contents": []},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    stubber.add_response("delete_bucket", {}, expected_params={"Bucket": "b"})
    with stubber:
        r2_client.delete_bucket(client, "b")
