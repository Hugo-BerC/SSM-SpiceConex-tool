from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from services.global_inventory import (
    INVENTORY_COLUMNS, collect_global_inventory, export_inventory, inventory_value, list_aggregators,
)


ACCOUNT = "111111111111"
KIND = "AWS::EC2::Instance"


def identifier(account=ACCOUNT, region="eu-west-1", kind=KIND, resource="i-test"):
    return dict(SourceAccountId=account, SourceRegion=region, ResourceType=kind, ResourceId=resource,
                ResourceName="=unsafe-name")


def detail(item):
    return dict(accountId=item["SourceAccountId"], awsRegion=item["SourceRegion"],
                resourceType=item["ResourceType"], resourceId=item["ResourceId"],
                resourceName=item.get("ResourceName", ""), configurationItemStatus="OK",
                arn=f"arn:aws:ec2:{item['SourceRegion']}:{item['SourceAccountId']}:instance/{item['ResourceId']}",
                configurationItemCaptureTime=datetime(2026, 1, 1, tzinfo=timezone.utc))


class GlobalInventoryTests(unittest.TestCase):
    def setUp(self):
        self.config, self.organizations = Mock(), Mock()
        self.session = Mock()
        self.session.client.side_effect = lambda service, **_kwargs: (
            self.config if service == "config" else self.organizations)
        self.factory = patch("services.global_inventory.get_boto3_session", return_value=self.session)
        self.factory.start(); self.addCleanup(self.factory.stop)
        self.config.describe_configuration_aggregators.return_value = {
            "ConfigurationAggregators": [{"ConfigurationAggregatorName": "all_accounts"}]}
        self.config.describe_configuration_aggregator_sources_status.return_value = {
            "AggregatedSourceStatusList": [{"SourceId": ACCOUNT, "SourceType": "ACCOUNT",
                                             "AwsRegion": "eu-west-1", "LastUpdateStatus": "SUCCEEDED"}]}
        self.organizations.list_accounts.return_value = {"Accounts": [{"Id": ACCOUNT, "Name": "IB"}]}
        self.config.get_aggregate_discovered_resource_counts.return_value = {
            "GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 1}]}
        self.config.list_aggregate_discovered_resources.return_value = {"ResourceIdentifiers": [identifier()]}
        self.config.batch_get_aggregate_resource_config.return_value = {
            "BaseConfigurationItems": [detail(identifier())]}
        self.config.select_aggregate_resource_config.return_value = {"Results": []}

    def collect(self, **kwargs):
        return collect_global_inventory("IB", "eu-west-1", "all_accounts", **kwargs)

    def test_basic_report_keeps_original_identity_and_capture_time(self):
        report = self.collect()
        self.assertEqual("COLLECTED", report["status"])
        row = report["resources"][0]
        self.assertEqual("IB", row["account_name"])
        self.assertEqual([ACCOUNT, "eu-west-1", KIND, "i-test"], json.loads(row["resource_key"]))
        self.assertEqual("2026-01-01T00:00:00+00:00", row["config_capture_time"])
        self.assertEqual("AVAILABLE", row["details_status"])
        self.assertNotIn("configuration", row)

    def test_duplicates_do_not_merge_accounts_regions_or_resource_types(self):
        items = [identifier(), identifier(), identifier(account="222222222222"), identifier(region="eu-south-2"),
                 identifier(kind="AWS::EC2::Volume")]
        self.config.get_aggregate_discovered_resource_counts.return_value = {
            "GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 4}]}
        self.config.list_aggregate_discovered_resources.return_value = {"ResourceIdentifiers": items}
        self.config.batch_get_aggregate_resource_config.return_value = {"BaseConfigurationItems": [detail(i) for i in items]}
        report = self.collect()
        self.assertEqual(4, len(report["resources"]))
        self.assertEqual(4, len({r["resource_key"] for r in report["resources"]}))

    def test_all_pages_of_counts_resources_and_source_status_are_read(self):
        self.config.get_aggregate_discovered_resource_counts.side_effect = [
            {"GroupedResourceCounts": [], "NextToken": "types-2"},
            {"GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 2}]}]
        second = identifier(resource="i-second")
        self.config.list_aggregate_discovered_resources.side_effect = [
            {"ResourceIdentifiers": [identifier()], "NextToken": "resources-2"}, {"ResourceIdentifiers": [second]}]
        self.config.describe_configuration_aggregator_sources_status.side_effect = [
            {"AggregatedSourceStatusList": [], "NextToken": "sources-2"},
            self.config.describe_configuration_aggregator_sources_status.return_value]
        self.config.batch_get_aggregate_resource_config.return_value = {
            "BaseConfigurationItems": [detail(identifier()), detail(second)]}
        self.assertEqual(2, len(self.collect()["resources"]))
        for method, token in ((self.config.get_aggregate_discovered_resource_counts, "types-2"),
                              (self.config.list_aggregate_discovered_resources, "resources-2"),
                              (self.config.describe_configuration_aggregator_sources_status, "sources-2")):
            self.assertEqual(token, method.call_args.kwargs["NextToken"])

    def test_detail_batches_are_limited_to_100_and_unprocessed_are_retried(self):
        items = [identifier(resource=f"i-{index}") for index in range(101)]
        self.config.get_aggregate_discovered_resource_counts.return_value = {
            "GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 101}]}
        self.config.list_aggregate_discovered_resources.return_value = {"ResourceIdentifiers": items}
        self.config.batch_get_aggregate_resource_config.side_effect = [
            {"BaseConfigurationItems": [detail(i) for i in items[:99]], "UnprocessedResourceIdentifiers": [items[99]]},
            {"BaseConfigurationItems": [detail(items[99])]}, {"BaseConfigurationItems": [detail(items[100])]}]
        with patch("services.global_inventory.Event.wait", return_value=False):
            report = self.collect()
        sizes = [len(call.kwargs["ResourceIdentifiers"]) for call in self.config.batch_get_aggregate_resource_config.call_args_list]
        self.assertEqual([100, 1, 1], sizes)
        self.assertEqual("COLLECTED", report["status"])

    def test_missing_details_keep_identifier_and_make_report_partial(self):
        self.config.batch_get_aggregate_resource_config.return_value = {"UnprocessedResourceIdentifiers": [identifier()]}
        with patch("services.global_inventory.Event.wait", return_value=False):
            report = self.collect()
        self.assertEqual(3, self.config.batch_get_aggregate_resource_config.call_count)
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual("MISSING", report["resources"][0]["details_status"])
        self.assertEqual("", report["resources"][0]["arn"])

    def test_denied_detail_request_preserves_basic_inventory(self):
        self.config.batch_get_aggregate_resource_config.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "synthetic-sensitive-diagnostic"}}, "BatchGetAggregateResourceConfig")
        report = self.collect()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(1, len(report["resources"]))
        self.assertNotIn("synthetic-sensitive-diagnostic", json.dumps(report))

    def test_listing_failure_preserves_earlier_page(self):
        self.config.list_aggregate_discovered_resources.side_effect = [
            {"ResourceIdentifiers": [identifier()], "NextToken": "second"}, RuntimeError("failed")]
        report = self.collect()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(1, len(report["resources"]))

    def test_outdated_or_unavailable_source_status_is_visible(self):
        self.config.describe_configuration_aggregator_sources_status.return_value = {
            "AggregatedSourceStatusList": [{"SourceId": "o-example", "SourceType": "ORGANIZATION",
                                             "AwsRegion": "eu-west-1", "LastUpdateStatus": "OUTDATED"}]}
        report = self.collect()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual("ORGANIZATION", report["coverage"]["sources"][0]["source_type"])

    def test_account_names_are_optional_and_do_not_prevent_inventory(self):
        self.organizations.list_accounts.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "ListAccounts")
        report = self.collect()
        self.assertEqual("COLLECTED", report["status"])
        self.assertEqual("UNAVAILABLE", report["coverage"]["account_names_status"])
        self.assertEqual("", report["resources"][0]["account_name"])

    def test_cancellation_keeps_identifiers_and_does_not_read_more_details(self):
        cancel = Event()
        def progress(message):
            if message.startswith("Listed"):
                cancel.set()
        report = self.collect(cancel=cancel, progress=progress)
        self.assertEqual("CANCELLED", report["status"])
        self.assertEqual(1, len(report["resources"]))
        self.config.batch_get_aggregate_resource_config.assert_not_called()

    def test_auth_recovery_recreates_client_and_retries_only_failed_request(self):
        expired = ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "BatchGetAggregateResourceConfig")
        self.config.batch_get_aggregate_resource_config.side_effect = [expired, {"BaseConfigurationItems": [detail(identifier())]}]
        def recover(_profile, operation):
            try:
                return operation()
            except ClientError:
                return operation()
        self.assertEqual("COLLECTED", self.collect(run_with_sso=recover)["status"])
        self.assertEqual(1, self.config.list_aggregate_discovered_resources.call_count)
        self.assertEqual(2, self.config.batch_get_aggregate_resource_config.call_count)
        self.assertEqual(3, self.session.client.call_count)  # config, organizations, refreshed config

    def test_export_round_trip_preserves_raw_json_and_protects_csv(self):
        report = self.collect()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            export_inventory(target / "inventory.json", report)
            data = json.loads((target / "inventory.json").read_text(encoding="utf-8"))
            self.assertEqual("=unsafe-name", data["resources"][0]["resource_name"])
            self.assertEqual("ALL", data["export"]["scope"])
            export_inventory(target / "inventory.csv", report)
            with (target / "inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual("'=unsafe-name", rows[0]["resource_name"])
            self.assertEqual(ACCOUNT, rows[0]["account_id"])
            self.assertTrue(rows[0]["inventory_export_time"])
            self.assertEqual("", report["resources"][0]["inventory_export_time"])
            export_inventory(target / "filtered.json", report, resources=[])
            filtered = json.loads((target / "filtered.json").read_text(encoding="utf-8"))
            self.assertEqual([], filtered["resources"])
            self.assertEqual(1, filtered["export"]["collected_resource_count"])

    def test_csv_uses_table_column_order_and_omits_internal_list_key(self):
        report = self.collect()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.csv"
            export_inventory(path, report)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                row = next(reader)
            self.assertEqual(list(INVENTORY_COLUMNS), reader.fieldnames)
            self.assertEqual("resource_type", reader.fieldnames[0])
            self.assertNotIn("resource_key", row)
            self.assertNotIn(report["resources"][0]["resource_key"], row.values())

    def test_arn_identifier_is_displayed_once_and_raw_values_are_preserved(self):
        arn = "arn:aws:sns:eu-west-1:111111111111:notifications"
        row = {"resource_id": arn, "arn": arn, "resource_name": "notifications"}
        self.assertEqual("", inventory_value(row, "resource_id"))
        self.assertEqual(arn, inventory_value(row, "arn"))
        self.assertEqual("", inventory_value(row, "config_resource_arn"))
        self.assertEqual(arn, row["resource_id"])
        self.assertEqual("notifications", inventory_value(row, "resource_name"))

    def test_different_config_arn_is_preserved_in_its_own_column(self):
        config_arn = "arn:aws:securityhub:eu-west-1:111111111111:subscription/example/1.0"
        arn = "arn:aws:securityhub:::standards/example/1.0"
        row = {"resource_id": config_arn, "arn": arn}
        self.assertEqual("", inventory_value(row, "resource_id"))
        self.assertEqual(arn, inventory_value(row, "arn"))
        self.assertEqual(config_arn, inventory_value(row, "config_resource_arn"))

    def test_arn_identifier_without_details_is_available_in_arn_column(self):
        arn = "arn:aws:resource-explorer-2:eu-west-1:111111111111:index/unique-id"
        row = {"resource_id": arn, "arn": "", "details_status": "MISSING"}
        self.assertEqual(arn, inventory_value(row, "arn"))
        self.assertEqual("", inventory_value(row, "resource_id"))
        self.assertEqual("MISSING", inventory_value(row, "details_status"))
        self.assertEqual("", row["arn"])

    def test_semicolon_export_quotes_text_and_keeps_account_ids_as_strings(self):
        report = self.collect()
        row = report["resources"][0]
        row.update(account_id="001234567890", resource_name='servicio; "Espa\u00f1a"\nlinea 2')
        for delimiter in (",", ";"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "inventory.csv"
                export_inventory(path, report, delimiter=delimiter)
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle, delimiter=delimiter)
                    values = list(reader)
                self.assertEqual(1, len(values))
                self.assertEqual(len(INVENTORY_COLUMNS), len(values[0]))
                self.assertEqual(row["resource_name"], values[0]["resource_name"])
                self.assertEqual("001234567890", values[0]["account_id"])

    def test_csv_and_json_keep_arns_only_in_arn_fields(self):
        report = self.collect()
        row = report["resources"][0]
        row["resource_id"] = row["arn"]
        with tempfile.TemporaryDirectory() as directory:
            csv_path, json_path = Path(directory) / "inventory.csv", Path(directory) / "inventory.json"
            export_inventory(csv_path, report)
            export_inventory(json_path, report)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                values = next(csv.DictReader(handle))
            self.assertEqual("", values["resource_id"])
            self.assertEqual(row["arn"], values["arn"])
            raw = json.loads(json_path.read_text(encoding="utf-8"))["resources"][0]
            self.assertEqual("", raw["resource_id"])
            self.assertEqual(row["arn"], raw["arn"])
            self.assertNotIn("resource_key", raw)
            self.assertEqual(row["arn"], report["resources"][0]["resource_id"])

    def test_failed_export_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text("original", encoding="utf-8")
            with patch("services.global_inventory.json.dump", side_effect=OSError("full disk")):
                with self.assertRaises(OSError):
                    export_inventory(path, self.collect())
            self.assertEqual("original", path.read_text(encoding="utf-8"))
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_aggregator_listing_paginates_and_validates_input(self):
        self.config.describe_configuration_aggregators.side_effect = [
            {"ConfigurationAggregators": [{"ConfigurationAggregatorName": "b"}], "NextToken": "next"},
            {"ConfigurationAggregators": [{"ConfigurationAggregatorName": "a"}]}]
        self.assertEqual(["a", "b"], [a["ConfigurationAggregatorName"] for a in list_aggregators("IB", "eu-west-1")])
        with self.assertRaises(ValueError):
            list_aggregators("", "eu-west-1")

    def test_requests_match_real_botocore_service_shapes(self):
        session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing", region_name="eu-west-1")
        client = session.client("config")
        org = session.client("organizations")
        stub, org_stub = Stubber(client), Stubber(org)
        stub.add_response("describe_configuration_aggregators", {"ConfigurationAggregators": [
            {"ConfigurationAggregatorName": "all_accounts"}]}, {"ConfigurationAggregatorNames": ["all_accounts"]})
        stub.add_response("describe_configuration_aggregator_sources_status", {"AggregatedSourceStatusList": []},
                          {"ConfigurationAggregatorName": "all_accounts", "Limit": 100})
        org_stub.add_response("list_accounts", {"Accounts": []}, {})
        stub.add_response("get_aggregate_discovered_resource_counts", {"TotalDiscoveredResources": 1, "GroupedResourceCounts": [
            {"GroupName": KIND, "ResourceCount": 1}]},
            {"ConfigurationAggregatorName": "all_accounts", "GroupByKey": "RESOURCE_TYPE", "Limit": 1000})
        stub.add_response("list_aggregate_discovered_resources", {"ResourceIdentifiers": [identifier()]},
                          {"ConfigurationAggregatorName": "all_accounts", "ResourceType": KIND, "Limit": 100})
        request_id = {key: value for key, value in identifier().items() if key != "ResourceName"}
        stub.add_response("batch_get_aggregate_resource_config", {"BaseConfigurationItems": [detail(identifier())]},
                          {"ConfigurationAggregatorName": "all_accounts", "ResourceIdentifiers": [request_id]})
        stub.add_response("select_aggregate_resource_config", {"Results": []}, {
            "ConfigurationAggregatorName": "all_accounts", "Limit": 100,
            "Expression": "SELECT accountId, awsRegion, resourceType, resourceId, tags"})
        with stub, org_stub, patch("services.global_inventory.get_boto3_session") as factory:
            factory.return_value.client.side_effect = lambda service, **_kw: client if service == "config" else org
            self.assertEqual(1, len(self.collect()["resources"]))
            stub.assert_no_pending_responses(); org_stub.assert_no_pending_responses()

    def test_empty_inventory_has_exportable_coverage_and_csv_headers(self):
        self.config.get_aggregate_discovered_resource_counts.return_value = {"GroupedResourceCounts": []}
        report = self.collect()
        self.assertEqual(0, report["summary"]["resource_count"])
        self.assertIn("does not certify", report["coverage"]["note"])
        self.config.list_aggregate_discovered_resources.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            export_inventory(path, report)
            self.assertIn("resource_id", path.read_text(encoding="utf-8-sig"))

    def test_failed_later_counts_page_keeps_known_types_and_reports_partial_scope(self):
        self.config.get_aggregate_discovered_resource_counts.side_effect = [
            {"GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 1}], "NextToken": "more"},
            RuntimeError("failed")]
        report = self.collect()
        self.assertEqual(1, len(report["resources"]))
        self.assertEqual("PARTIAL", report["status"])
        self.assertTrue(any(warning["stage"] == "resource_type_counts" for warning in report["warnings"]))

    def test_expected_total_survives_a_failed_resource_type_counts_page(self):
        self.config.get_aggregate_discovered_resource_counts.side_effect = [
            {"TotalDiscoveredResources": 70001, "GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 1}], "NextToken": "more"},
            RuntimeError("failed")]
        report = self.collect()
        self.assertEqual(70001, report["summary"]["expected_resource_count"])
        self.assertEqual(1, report["summary"]["resource_count"])
        self.assertEqual("PARTIAL", report["status"])
        self.assertTrue(any(w["stage"] == "inventory_count_mismatch" for w in report["warnings"]))

    def test_repeated_pagination_token_stops_and_retains_partial_results(self):
        self.config.list_aggregate_discovered_resources.return_value = {
            "ResourceIdentifiers": [identifier()], "NextToken": "repeated"}
        report = self.collect()
        self.assertEqual(2, self.config.list_aggregate_discovered_resources.call_count)
        self.assertEqual(1, len(report["resources"]))
        self.assertEqual("PARTIAL", report["status"])

    def test_persistent_expiry_after_listing_does_not_discard_collected_identifiers(self):
        self.config.batch_get_aggregate_resource_config.side_effect = ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "BatchGetAggregateResourceConfig")
        report = self.collect()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(1, len(report["resources"]))
        self.assertTrue(any(warning["stage"] == "collection_stopped" for warning in report["warnings"]))

    def test_cancelled_coverage_counts_details_retained_from_completed_batches(self):
        items = [identifier(resource=f"i-{index}") for index in range(101)]
        self.config.get_aggregate_discovered_resource_counts.return_value = {
            "GroupedResourceCounts": [{"GroupName": KIND, "ResourceCount": 101}]}
        self.config.list_aggregate_discovered_resources.return_value = {"ResourceIdentifiers": items}
        self.config.batch_get_aggregate_resource_config.return_value = {
            "BaseConfigurationItems": [detail(item) for item in items[:100]]}
        cancel = Event()
        def progress(message):
            if "101/101" in message:
                cancel.set()
        report = self.collect(cancel=cancel, progress=progress)
        self.assertEqual("CANCELLED", report["status"])
        self.assertEqual(101, report["coverage"]["resource_types"][0]["listed_count"])
        self.assertEqual(100, report["coverage"]["resource_types"][0]["details_count"])
        self.config.batch_get_aggregate_resource_config.assert_called_once()

    def test_ec2_real_name_uses_name_tag_and_preserves_id_and_arn(self):
        source = detail(identifier())
        source["resourceName"] = "native-name"
        self.config.batch_get_aggregate_resource_config.return_value = {"BaseConfigurationItems": [source]}
        self.config.select_aggregate_resource_config.return_value = {"Results": [json.dumps({
            "accountId": ACCOUNT, "awsRegion": "eu-west-1", "resourceType": KIND, "resourceId": "i-test",
            "tags": {"Name": "payments-production-01", "Environment": "production"},
        })]}
        report = self.collect()
        row = report["resources"][0]
        self.assertEqual("payments-production-01", row["resource_name"])
        self.assertEqual("i-test", row["resource_id"])
        self.assertEqual(source["arn"], row["arn"])
        self.assertEqual("AVAILABLE", report["coverage"]["name_tags_status"])

    def test_name_query_paginates_and_never_adds_resources_to_inventory(self):
        self.config.select_aggregate_resource_config.side_effect = [
            {"Results": [json.dumps({"accountId": "222222222222", "awsRegion": "eu-west-1",
                                      "resourceType": KIND, "resourceId": "i-test", "tags": {"Name": "wrong-account"}})],
             "NextToken": "names-next"},
            {"Results": [json.dumps({"accountId": ACCOUNT, "awsRegion": "eu-west-1",
                                      "resourceType": KIND, "resourceId": "i-test",
                                      "tags": [{"key": "Name", "value": "correct-name"}]})]}]
        report = self.collect()
        self.assertEqual(1, len(report["resources"]))
        self.assertEqual("correct-name", report["resources"][0]["resource_name"])
        self.assertEqual("names-next", self.config.select_aggregate_resource_config.call_args.kwargs["NextToken"])

    def test_no_tag_or_native_name_leaves_name_empty_instead_of_copying_id_or_arn(self):
        item = identifier()
        item.pop("ResourceName")
        data = detail(item)
        data["resourceName"] = data["arn"]
        self.config.list_aggregate_discovered_resources.return_value = {"ResourceIdentifiers": [item]}
        self.config.batch_get_aggregate_resource_config.return_value = {"BaseConfigurationItems": [data]}
        row = self.collect()["resources"][0]
        self.assertEqual("", row["resource_name"])
        self.assertEqual("i-test", row["resource_id"])
        self.assertTrue(row["arn"].startswith("arn:"))

    def test_failed_name_query_preserves_native_names_and_inventory(self):
        self.config.select_aggregate_resource_config.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "SelectAggregateResourceConfig")
        report = self.collect()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual("UNAVAILABLE", report["coverage"]["name_tags_status"])
        self.assertEqual("=unsafe-name", report["resources"][0]["resource_name"])
        self.assertEqual(1, len(report["resources"]))

    def test_global_details_and_name_tags_match_source_region_without_merging_observations(self):
        items = [identifier(kind="AWS::IAM::Role"), identifier(region="eu-south-2", kind="AWS::IAM::Role")]
        self.config.get_aggregate_discovered_resource_counts.return_value = {"GroupedResourceCounts": [
            {"GroupName": "AWS::IAM::Role", "ResourceCount": 2}]}
        self.config.list_aggregate_discovered_resources.return_value = {"ResourceIdentifiers": items}
        global_detail = detail(items[0]); global_detail["awsRegion"] = "global"
        global_detail["arn"] = f"arn:aws:iam::{ACCOUNT}:role/example"
        self.config.batch_get_aggregate_resource_config.return_value = {"BaseConfigurationItems": [global_detail]}
        self.config.select_aggregate_resource_config.return_value = {"Results": [json.dumps({
            "accountId": ACCOUNT, "awsRegion": "global", "resourceType": "AWS::IAM::Role", "resourceId": "i-test",
            "tags": [{"Key": "Name", "Value": "global-role-name"}],
        })]}
        report = self.collect()
        self.assertEqual(2, len(report["resources"]))
        self.assertEqual({"eu-west-1", "eu-south-2"}, {row["region"] for row in report["resources"]})
        for row in report["resources"]:
            self.assertEqual("AVAILABLE", row["details_status"])
            self.assertEqual("global-role-name", row["resource_name"])
        self.assertEqual(2, report["coverage"]["resource_types"][0]["details_count"])

    def test_malformed_name_query_values_are_visible_without_losing_valid_names(self):
        self.config.select_aggregate_resource_config.return_value = {"Results": ["invalid-json", "null", json.dumps({
            "accountId": ACCOUNT, "awsRegion": "eu-west-1", "resourceType": KIND, "resourceId": "i-test",
            "tags": json.dumps({"Name": "valid-name"}),
        })]}
        report = self.collect()
        self.assertEqual("PARTIAL", report["coverage"]["name_tags_status"])
        self.assertEqual("valid-name", report["resources"][0]["resource_name"])

    def test_name_query_cancel_retains_names_already_resolved(self):
        cancel = Event()
        self.config.select_aggregate_resource_config.return_value = {"Results": [json.dumps({
            "accountId": ACCOUNT, "awsRegion": "eu-west-1", "resourceType": KIND, "resourceId": "i-test",
            "tags": {"Name": "retained-name"},
        })], "NextToken": "more"}
        def progress(message):
            if "1 resource observations named" in message:
                cancel.set()
        report = self.collect(cancel=cancel, progress=progress)
        self.assertEqual("CANCELLED", report["status"])
        self.assertEqual("PARTIAL", report["coverage"]["name_tags_status"])
        self.assertEqual("retained-name", report["resources"][0]["resource_name"])

    def test_exported_names_never_contain_arn_and_distinct_arns_survive_reexport(self):
        report = self.collect()
        row = report["resources"][0]
        row.update(resource_id="arn:aws:securityhub:eu-west-1:111111111111:subscription/example",
                   arn="arn:aws:securityhub:::standards/example", resource_name="arn:aws:example:invalid-name")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            export_inventory(path, report)
            first = json.loads(path.read_text(encoding="utf-8"))
            values = first["resources"][0]
            self.assertEqual("2.0", first["schema_version"])
            self.assertEqual("", values["resource_id"])
            self.assertEqual("", values["resource_name"])
            self.assertEqual(row["resource_id"], values["config_resource_arn"])
            export_inventory(path, first)
            second = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(values["config_resource_arn"], second["resources"][0]["config_resource_arn"])


if __name__ == "__main__":
    unittest.main()
