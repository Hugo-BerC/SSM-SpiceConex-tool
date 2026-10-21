from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pyside6_poc.dr_dashboard import DRDashboardPage
from services.discovery.engine import discover_infrastructure
from services.discovery.models import InfrastructureGraph, ResourceEdge, ResourceNode
from services.dr_dashboard import ComparisonPair, MetricResult, build_dashboard, build_metrics, build_pair_metrics, format_metric_value, publish_new_dashboard, query_metrics, render_html_report


ACCOUNT = "123456789012"
REGIONS = ("eu-west-1", "eu-south-2")
TAG = "ib:resource:application"


def node(identifier, kind, region=REGIONS[0], arn=None, tags=None):
    return ResourceNode(identifier, arn, kind, identifier, region, ACCOUNT, tags or {})


class DRDashboardTests(unittest.TestCase):
    def test_metric_values_are_human_readable_without_scientific_notation(self):
        self.assertEqual("1,100", format_metric_value(1100))
        self.assertEqual("390,000", format_metric_value(390000))
        self.assertEqual("0.002", format_metric_value(0.00239))

    def test_only_api_gateway_metrics_are_eligible_for_dr_evidence(self):
        nodes = [
            node("orders-rest", "APIGateway", tags={TAG: "orders"}),
            node("orders-http", "APIGatewayV2", REGIONS[1], tags={TAG: "orders"}),
            node("alb", "ALB"), node("i-123", "EC2"), node("unsupported", "ECS"),
        ]
        metrics, skipped = build_metrics(nodes, REGIONS, "orders", TAG)
        self.assertEqual({"APIGateway", "APIGatewayV2"}, {metric.resource_type for metric in metrics})
        self.assertEqual({"Count", "Latency", "4XXError", "5XXError", "4xx", "5xx"}, {metric.name for metric in metrics})
        self.assertTrue(all(metric.attribution == "TAGGED" for metric in metrics))
        self.assertEqual(3, len(skipped))
        with self.assertRaises(ValueError):
            build_metrics(nodes, (REGIONS[0], REGIONS[0]))

    def test_dashboard_series_regions_and_no_logs(self):
        metrics, _ = build_metrics([node("api-a", "APIGateway"), node("api-b", "APIGateway", REGIONS[1])], REGIONS)
        body = build_dashboard(ACCOUNT, "all", REGIONS, metrics)
        self.assertTrue(all(widget["type"] in {"text", "metric"} for widget in body["widgets"]))
        widget = next(w for w in body["widgets"] if w["type"] == "metric")
        self.assertEqual(set(REGIONS), {entry[-1]["region"] for entry in widget["properties"]["metrics"]})
        self.assertTrue(all("ACCOUNT SCOPE" in entry[-1]["label"] for entry in widget["properties"]["metrics"]))

    def test_query_distinguishes_missing_result_no_data_and_partial_data(self):
        metrics, _ = build_metrics([node("api-a", "APIGateway")], REGIONS)
        session = MagicMock()
        sts = MagicMock(); sts.get_caller_identity.return_value = {"Account": ACCOUNT}
        cloudwatch = MagicMock()
        now = datetime.now(timezone.utc)
        cloudwatch.get_metric_data.return_value = {"MetricDataResults": [
            {"Id": "m0", "StatusCode": "Complete", "Timestamps": [now], "Values": [42]},
            {"Id": "m1", "StatusCode": "Complete", "Timestamps": [], "Values": []},
            {"Id": "m2", "StatusCode": "PartialData", "Timestamps": [], "Values": []},
        ]}
        session.client.side_effect = lambda service, **kwargs: sts if service == "sts" else cloudwatch
        with patch("services.dr_dashboard.get_boto3_session", return_value=session):
            results = query_metrics("profile", ACCOUNT, metrics, now - timedelta(hours=1), now)
        self.assertEqual(["DATA", "NO DATA", "INCOMPLETE", "INCOMPLETE"], [r.status for r in results])
        cloudwatch.put_dashboard.assert_not_called()

    def test_publish_refuses_account_mismatch(self):
        session = MagicMock(); session.client.return_value.get_caller_identity.return_value = {"Account": "different"}
        with patch("services.dr_dashboard.get_boto3_session", return_value=session):
            with self.assertRaisesRegex(ValueError, "account changed"):
                publish_new_dashboard("profile", ACCOUNT, REGIONS[0], "orders", {})
        session.client.return_value.put_dashboard.assert_not_called()

    def test_publish_uses_profile_dashboard_name_after_identity_check(self):
        session = MagicMock(); sts = MagicMock(); cloudwatch = MagicMock()
        sts.get_caller_identity.return_value = {"Account": ACCOUNT}
        cloudwatch.put_dashboard.return_value = {"DashboardValidationMessages": []}
        session.client.side_effect = lambda service, **kwargs: sts if service == "sts" else cloudwatch
        with patch("services.dr_dashboard.get_boto3_session", return_value=session):
            name, warnings = publish_new_dashboard("profile", ACCOUNT, REGIONS[0], "orders", {"widgets": []})
        self.assertEqual("profile-Disaster_Recovery", name)
        self.assertEqual([], warnings)
        self.assertEqual(name, cloudwatch.put_dashboard.call_args.kwargs["DashboardName"])

    def test_explicit_pair_generates_two_series_per_full_width_widget(self):
        source = node("api-source", "APIGateway", REGIONS[0], tags={TAG: "orders"})
        source.name = "orders-source"
        dr = node("api-dr", "APIGateway", REGIONS[1], tags={TAG: "orders"})
        dr.name = "orders-recovery"
        metrics, skipped = build_pair_metrics([ComparisonPair(source, dr, "orders public API")], REGIONS, "orders", TAG)
        self.assertFalse(skipped)
        body = build_dashboard(ACCOUNT, "orders", REGIONS, metrics)
        widgets = [widget for widget in body["widgets"] if widget["type"] == "metric"]
        self.assertEqual(4, len(widgets))
        self.assertTrue(all(widget["x"] == 0 and widget["width"] == 24 for widget in widgets))
        self.assertTrue(all(len(widget["properties"]["metrics"]) == 2 for widget in widgets))
        self.assertTrue(all({entry[-1]["region"] for entry in widget["properties"]["metrics"]} == set(REGIONS) for widget in widgets))

    def test_non_api_resources_are_skipped_from_dr_evidence(self):
        source = node("alb-a", "ALB", REGIONS[0])
        dr = node("alb-b", "ALB", REGIONS[1])
        metrics, skipped = build_pair_metrics([ComparisonPair(source, dr, "internal ALB")], REGIONS)
        self.assertEqual([], metrics)
        self.assertEqual(2, len(skipped))

    def test_api_comparison_set_aggregates_many_apis_into_two_regional_series(self):
        source_a = node("api-a", "APIGateway", REGIONS[0])
        source_b = node("api-b", "APIGateway", REGIONS[0])
        dr_a = node("api-dr-a", "APIGateway", REGIONS[1])
        dr_b = node("api-dr-b", "APIGateway", REGIONS[1])
        selection = ComparisonPair(source_a, dr_a, "public APIs", (source_a, source_b), (dr_a, dr_b))
        metrics, skipped = build_pair_metrics([selection], REGIONS)
        self.assertFalse(skipped)
        body = build_dashboard(ACCOUNT, "orders", REGIONS, metrics)
        widgets = [widget for widget in body["widgets"] if widget["type"] == "metric"]
        self.assertEqual(4, len(widgets))
        self.assertTrue(all(len(widget["properties"]["metrics"]) == 6 for widget in widgets))
        calls = next(widget for widget in widgets if "API calls" in widget["properties"]["title"])
        self.assertTrue(any("SUM" in item[0].get("expression", "") for item in calls["properties"]["metrics"] if isinstance(item[0], dict)))

    def test_html_report_contains_both_regions_and_no_data(self):
        metrics, _ = build_pair_metrics([ComparisonPair(node("api-a", "APIGateway"), node("api-b", "APIGateway", REGIONS[1]), "API")], REGIONS)
        now = datetime.now(timezone.utc)
        report = render_html_report("profile", ACCOUNT, "orders", REGIONS, now - timedelta(minutes=10), now, [MetricResult(metrics[0], "DATA", [(now, 4.0)]), MetricResult(metrics[4], "NO DATA")])
        self.assertIn("profile — Disaster Recovery evidence", report)
        self.assertIn(REGIONS[0], report); self.assertIn(REGIONS[1], report); self.assertIn("NO DATA", report)

    def test_html_report_draws_two_region_api_gateway_charts(self):
        source = node("api-source", "APIGateway", REGIONS[0])
        dr = node("api-dr", "APIGateway", REGIONS[1])
        metrics, _ = build_pair_metrics([ComparisonPair(source, dr, "public API")], REGIONS)
        selected = [metric for metric in metrics if metric.name in {"Count", "Latency"}]
        now = datetime.now(timezone.utc)
        results = [MetricResult(metric, "DATA", [(now - timedelta(minutes=5), 5.0), (now, 8.0)]) for metric in selected]
        report = render_html_report("profile", ACCOUNT, "orders", REGIONS, now - timedelta(minutes=10), now, results)
        self.assertIn("API Gateway calls", report)
        self.assertIn("API Gateway response time / tm99", report)
        self.assertIn("<svg", report)

    def test_discovery_queries_only_selected_regions(self):
        session = MagicMock(); session.client.return_value.get_caller_identity.return_value = {"Account": ACCOUNT}
        with patch("services.discovery.engine.get_boto3_session", return_value=session), \
             patch("services.discovery.engine._discover_region") as regional, \
             patch("services.discovery.engine._discover_global"):
            graph = discover_infrastructure("profile", REGIONS[0], "SELECTED REGIONS", TAG, list(REGIONS))
        self.assertEqual(set(REGIONS), set(graph.regions))
        self.assertEqual(set(REGIONS), {call.args[2] for call in regional.call_args_list})
        self.assertEqual(2, regional.call_count)


class DRPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_scope_defaults_and_stale_results_are_discarded(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        graph.add_node(node("api-111", "APIGateway", tags={TAG: "orders"}))
        graph.add_node(node("api-222", "APIGateway", REGIONS[1]))
        graph.add_edge(ResourceEdge("api-111", "api-222", "connected"))
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.preferred_regions = REGIONS
        page.set_context(graph, "orders", TAG)
        self.assertEqual(REGIONS, (page.region_a.currentText(), page.region_b.currentText()))
        states = {page.resources.item(i, 3).text(): page.resources.item(i, 0).checkState() for i in range(page.resources.rowCount())}
        self.assertEqual(Qt.CheckState.Unchecked, states["api-111"])
        self.assertEqual(Qt.CheckState.Unchecked, states["api-222"])
        old_revision = page._revision
        shared_row = next(i for i in range(page.resources.rowCount()) if page.resources.item(i, 3).text() == "api-222")
        page.resources.item(shared_row, 0).setCheckState(Qt.CheckState.Checked)
        page._prepared({"context": page.context_key, "profile": "profile", "revision": old_revision})
        self.assertIsNone(page.prepared)
        self.assertIn("discarded", page.context.text())

    def test_region_catalogue_is_available_but_undiscovered_region_is_blocked(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", [REGIONS[0]])
        graph.add_node(node("api-111", "APIGateway"))
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        self.assertGreaterEqual(page.region_b.count(), 3)
        page.region_b.setCurrentText("eu-central-1")
        self.assertIn("has not been discovered", page.context.text())
        self.assertEqual(0, page.resources.rowCount())

    def test_resource_filters_hide_rows_without_losing_selection(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        graph.add_node(node("orders-rest", "APIGateway", tags={TAG: "orders"}))
        graph.add_node(node("orders-http", "APIGatewayV2", REGIONS[1]))
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        page.resource_filter.setText("orders-http")
        visible_rows = [row for row in range(page.resources.rowCount()) if not page.resources.isRowHidden(row)]
        self.assertEqual(1, len(visible_rows))
        self.assertEqual("orders-http", page.resources.item(visible_rows[0], 3).text())
        page.resource_filter.clear(); page.resource_type_filter.setCurrentText("APIGateway")
        self.assertEqual(1, sum(not page.resources.isRowHidden(row) for row in range(page.resources.rowCount())))
        api_row = next(row for row in range(page.resources.rowCount()) if page.resources.item(row, 3).text() == "orders-rest")
        page.resources.item(api_row, 0).setCheckState(Qt.CheckState.Checked)
        page.selected_only.setChecked(True)
        self.assertEqual(1, sum(not page.resources.isRowHidden(row) for row in range(page.resources.rowCount())))
        self.assertEqual(Qt.CheckState.Checked, page.resources.item(api_row, 0).checkState())

    def test_dr_resource_selector_hides_raw_aws_config_types(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        graph.add_node(node("api-111", "APIGateway"))
        graph.add_node(node("opaque", "AWS::Unmodelled::Resource", REGIONS[1]))
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        displayed_types = {page.resources.item(row, 2).text() for row in range(page.resources.rowCount())}
        self.assertEqual({"APIGateway"}, displayed_types)

    def test_dr_resource_selector_deduplicates_config_and_live_api_gateway(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        arn = "arn:aws:apigateway:eu-west-1::/restapis/abc123"
        # Config may omit the API ARN from its partial inventory record.
        config_node = node("config-api", "APIGateway", REGIONS[0])
        config_node.name = "orders-api"; config_node.metadata["DiscoverySource"] = "AWS Config"
        live_node = node("abc123", "APIGateway", REGIONS[0], arn)
        live_node.name = "orders-api"; live_node.metadata["ApiId"] = "abc123"
        graph.add_node(config_node); graph.add_node(live_node)
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        self.assertEqual(1, page.resources.rowCount())
        self.assertEqual("orders-api", page.resources.item(0, 3).text())

    def test_user_can_build_explicit_pair_with_different_resource_names(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        graph.add_node(node("api-source", "APIGateway", REGIONS[0]))
        graph.nodes["api-source"].name = "api-primary"
        graph.add_node(node("api-dr", "APIGateway", REGIONS[1]))
        graph.nodes["api-dr"].name = "api-recovery"
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.preferred_regions = REGIONS; page.set_context(graph, "ALL PRODUCTS", TAG)
        for row in range(page.resources.rowCount()): page.resources.item(row, 0).setCheckState(Qt.CheckState.Checked)
        page.add_comparison_pair()
        self.assertEqual(1, len(page.comparison_pairs))
        self.assertEqual("api-primary ↔ api-recovery", page.comparison_pairs[0].label)
        self.assertEqual(1, page.pairs.rowCount())
        self.assertTrue(all(page.resources.item(row, 0).checkState() == Qt.CheckState.Unchecked for row in range(page.resources.rowCount())))

    def test_user_can_build_a_multi_api_comparison_set(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        for identifier, region in (("api-source-a", REGIONS[0]), ("api-source-b", REGIONS[0]), ("api-dr-a", REGIONS[1])):
            graph.add_node(node(identifier, "APIGateway", region))
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.preferred_regions = REGIONS; page.set_context(graph, "ALL PRODUCTS", TAG)
        for row in range(page.resources.rowCount()):
            page.resources.item(row, 0).setCheckState(Qt.CheckState.Checked)
        page.add_comparison_pair()
        self.assertEqual(1, len(page.comparison_pairs))
        selection = page.comparison_pairs[0]
        self.assertEqual(2, len(selection.sources))
        self.assertEqual(1, len(selection.drs))
        self.assertIn("APIGateway set (2 source / 1 DR)", selection.label)

    def test_compact_pair_controls_do_not_consume_table_space_initially(self):
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        self.assertGreaterEqual(page.resources.minimumHeight(), 260)
        self.assertTrue(page.pair_section.isHidden())
        self.assertTrue(page.actions_panel.isHidden())

    def test_metric_preview_renders_points_and_evidence_context(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        api = node("api-111", "APIGateway"); graph.add_node(api)
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        metrics, _ = build_metrics([api], REGIONS)
        now = datetime.now(timezone.utc)
        result = MetricResult(metrics[0], "DATA", [(now - timedelta(minutes=5), 2.0)])
        page._prepared({"context": page.context_key, "profile": "profile", "revision": page._revision,
            "results": [result], "regions": REGIONS, "start": now - timedelta(hours=1), "end": now,
            "period": 300, "account": ACCOUNT, "application": "ALL PRODUCTS", "skipped": []})
        self.assertTrue(page.export_png.isEnabled())
        self.assertGreater(len(page.preview.scene.items()), 0)
        self.assertEqual("DATA", page.metric_table.item(0, 3).text())


if __name__ == "__main__":
    unittest.main()
