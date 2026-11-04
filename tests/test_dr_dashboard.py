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
from services.dr_dashboard import ComparisonPair, MetricResult, build_dashboard, build_metrics, build_pair_metrics, format_metric_value, publish_new_dashboard, query_metrics, render_html_report, report_metric_key


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

    def test_metric_capable_architectural_resources_are_eligible_for_dr_evidence(self):
        nodes = [
            node("orders-rest", "APIGateway", tags={TAG: "orders"}),
            node("orders-http", "APIGatewayV2", REGIONS[1], tags={TAG: "orders"}),
            node("i-123", "EC2", tags={TAG: "orders"}),
            node("database", "RDS", REGIONS[1], tags={TAG: "orders"}),
            node("eni-unsupported", "NetworkInterface"),
        ]
        metrics, skipped = build_metrics(nodes, REGIONS, "orders", TAG)
        self.assertEqual({"APIGateway", "APIGatewayV2", "EC2", "RDS"}, {metric.resource_type for metric in metrics})
        self.assertIn("CPUUtilization", {metric.name for metric in metrics})
        self.assertIn("DatabaseConnections", {metric.name for metric in metrics})
        self.assertTrue(all(metric.attribution == "TAGGED" for metric in metrics))
        self.assertEqual(1, len(skipped))
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

    def test_explicit_pair_generates_two_series_in_two_column_widgets(self):
        source = node("api-source", "APIGateway", REGIONS[0], tags={TAG: "orders"})
        source.name = "orders-source"
        dr = node("api-dr", "APIGateway", REGIONS[1], tags={TAG: "orders"})
        dr.name = "orders-recovery"
        metrics, skipped = build_pair_metrics([ComparisonPair(source, dr, "orders public API")], REGIONS, "orders", TAG)
        self.assertFalse(skipped)
        body = build_dashboard(ACCOUNT, "orders", REGIONS, metrics)
        widgets = [widget for widget in body["widgets"] if widget["type"] == "metric"]
        self.assertEqual(4, len(widgets))
        self.assertTrue(all(widget["width"] == 12 for widget in widgets))
        self.assertEqual([0, 12, 0, 12], [widget["x"] for widget in widgets])
        self.assertEqual([4, 4, 10, 10], [widget["y"] for widget in widgets])
        self.assertTrue(all(len(widget["properties"]["metrics"]) == 2 for widget in widgets))
        self.assertTrue(all({entry[-1]["region"] for entry in widget["properties"]["metrics"]} == set(REGIONS) for widget in widgets))
        self.assertEqual("-PT6H", body["start"])
        self.assertNotIn("end", body)

    def test_load_balancers_have_type_specific_native_metrics(self):
        cases = (
            ("ALB", "app/source/aaa", "app/recovery/bbb", "AWS/ApplicationELB", "RequestCount"),
            ("NLB", "net/source/aaa", "net/recovery/bbb", "AWS/NetworkELB", "NewFlowCount"),
            ("GWLB", "gwy/source/aaa", "gwy/recovery/bbb", "AWS/GatewayELB", "RejectedFlowCount"),
        )
        for resource_type, source_suffix, dr_suffix, namespace, expected_metric in cases:
            source = node(f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:loadbalancer/{source_suffix}", resource_type, REGIONS[0])
            dr = node(f"arn:aws:elasticloadbalancing:{REGIONS[1]}:{ACCOUNT}:loadbalancer/{dr_suffix}", resource_type, REGIONS[1])
            metrics, skipped = build_pair_metrics([ComparisonPair(source, dr, resource_type)], REGIONS)
            self.assertFalse(skipped)
            self.assertEqual({namespace}, {metric.namespace for metric in metrics})
            self.assertIn(expected_metric, {metric.name for metric in metrics})
            self.assertEqual({source_suffix, dr_suffix}, {metric.dimension_value for metric in metrics})

    def test_alb_target_group_metrics_use_load_balancer_and_target_group_dimensions(self):
        source = node(
            f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:targetgroup/source/aaa",
            "ALBTargetGroup", REGIONS[0],
        )
        dr = node(
            f"arn:aws:elasticloadbalancing:{REGIONS[1]}:{ACCOUNT}:targetgroup/recovery/bbb",
            "ALBTargetGroup", REGIONS[1],
        )
        source.metadata["LoadBalancerArns"] = (
            f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:loadbalancer/app/source/111",
        )
        dr.metadata["LoadBalancerArns"] = (
            f"arn:aws:elasticloadbalancing:{REGIONS[1]}:{ACCOUNT}:loadbalancer/app/recovery/222",
        )
        metrics, skipped = build_pair_metrics([ComparisonPair(source, dr, "ALB target groups")], REGIONS)
        self.assertFalse(skipped)
        self.assertIn("HTTPCode_Target_2XX_Count", {metric.name for metric in metrics})
        self.assertIn("HTTPCode_Target_4XX_Count", {metric.name for metric in metrics})
        self.assertIn("TargetResponseTime", {metric.name for metric in metrics})
        self.assertEqual(
            (("LoadBalancer", "app/source/111"), ("TargetGroup", "targetgroup/source/aaa")),
            metrics[0].dimensions,
        )

    def test_structural_resources_without_metric_templates_are_skipped(self):
        source = node("subnet-a", "Subnet", REGIONS[0])
        dr = node("subnet-b", "Subnet", REGIONS[1])
        metrics, skipped = build_pair_metrics([ComparisonPair(source, dr, "network structure")], REGIONS)
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

    def test_ecs_utilization_shows_services_and_keeps_average_disabled(self):
        def ecs_service(identifier, name, region, cluster):
            service = node(identifier, "ECS", region)
            service.name = name
            service.metadata["ClusterArn"] = f"arn:aws:ecs:{region}:{ACCOUNT}:cluster/{cluster}"
            return service

        source_a = ecs_service("source-a", "orders-a", REGIONS[0], "source")
        source_b = ecs_service("source-b", "orders-b", REGIONS[0], "source")
        dr_a = ecs_service("dr-a", "orders-a", REGIONS[1], "recovery")
        dr_b = ecs_service("dr-b", "orders-b", REGIONS[1], "recovery")
        selection = ComparisonPair(source_a, dr_a, "ECS services", (source_a, source_b), (dr_a, dr_b))
        metrics, skipped = build_pair_metrics([selection], REGIONS)
        self.assertFalse(skipped)
        body = build_dashboard(ACCOUNT, "orders", REGIONS, metrics)
        memory = next(widget for widget in body["widgets"] if widget["type"] == "metric" and "Memory utilization" in widget["properties"]["title"])
        raw = [entry[-1] for entry in memory["properties"]["metrics"] if not isinstance(entry[0], dict)]
        expressions = [entry[0] for entry in memory["properties"]["metrics"] if isinstance(entry[0], dict)]
        self.assertTrue(all(item["visible"] for item in raw))
        self.assertTrue(all(not item["visible"] for item in expressions))
        self.assertTrue(all(" | orders-" in item["label"] for item in raw))

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

    def test_html_report_only_contains_operator_selected_metrics(self):
        source = node("api-source", "APIGateway", REGIONS[0])
        dr = node("api-dr", "APIGateway", REGIONS[1])
        metrics, _ = build_pair_metrics([ComparisonPair(source, dr, "public API")], REGIONS)
        selected = [metric for metric in metrics if metric.name in {"Count", "Latency"}]
        now = datetime.now(timezone.utc)
        results = [MetricResult(metric, "DATA", [(now - timedelta(minutes=5), 3.0), (now, 7.0)]) for metric in selected]
        count_key = report_metric_key(next(metric for metric in selected if metric.name == "Count"))
        report = render_html_report(
            "profile", ACCOUNT, "orders", REGIONS, now - timedelta(minutes=10), now,
            results, selected_metric_keys={count_key},
        )
        self.assertIn("API Gateway calls", report)
        self.assertNotIn("Response time / tm99</th>", report)
        self.assertEqual(1, report.count("<svg"))

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
        graph.add_node(node("i-123", "EC2"))
        graph.add_node(node("database", "RDS", REGIONS[1]))
        graph.add_node(node("subnet-123", "Subnet"))
        graph.add_node(node("opaque", "AWS::Unmodelled::Resource", REGIONS[1]))
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        displayed_types = {page.resources.item(row, 2).text() for row in range(page.resources.rowCount())}
        self.assertEqual({"APIGateway", "EC2", "RDS"}, displayed_types)

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

    def test_alb_filter_can_switch_between_load_balancers_and_target_groups(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        alb = node(f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:loadbalancer/app/orders/111", "ALB")
        target = node(f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:targetgroup/orders/222", "ALBTargetGroup")
        target.metadata["LoadBalancerArns"] = (alb.id,)
        graph.add_node(alb); graph.add_node(target)
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        self.assertEqual(2, sum(not page.resources.isRowHidden(row) for row in range(page.resources.rowCount())))
        page.resource_type_filter.setCurrentText("ALB")
        self.assertFalse(page.alb_metric_level.isHidden())
        self.assertEqual(["ALB"], [page.resources.item(row, 2).text() for row in range(page.resources.rowCount()) if not page.resources.isRowHidden(row)])
        page.alb_metric_level.setCurrentText("TARGET GROUPS")
        self.assertEqual(["ALB TARGET GROUP"], [page.resources.item(row, 2).text() for row in range(page.resources.rowCount()) if not page.resources.isRowHidden(row)])

    def test_nlb_filter_exposes_only_nlb_target_groups(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        nlb = node(f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:loadbalancer/net/orders/111", "NLB")
        target = node(f"arn:aws:elasticloadbalancing:{REGIONS[0]}:{ACCOUNT}:targetgroup/orders/222", "NLBTargetGroup")
        target.metadata["LoadBalancerArns"] = (nlb.id,)
        graph.add_node(nlb); graph.add_node(target)
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        page.resource_type_filter.setCurrentText("NLB")
        self.assertEqual(["NLB"], [page.resources.item(row, 2).text() for row in range(page.resources.rowCount()) if not page.resources.isRowHidden(row)])
        page.alb_metric_level.setCurrentText("TARGET GROUPS")
        self.assertEqual(["NLB TARGET GROUP"], [page.resources.item(row, 2).text() for row in range(page.resources.rowCount()) if not page.resources.isRowHidden(row)])

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
        self.assertFalse(page.report_metric_panel.isHidden())
        self.assertEqual(1, len(page.report_metric_controls))
        self.assertTrue(page.export_html.isEnabled())

    def test_ecs_average_utilization_report_metrics_are_off_by_default(self):
        graph = InfrastructureGraph(ACCOUNT, "profile", list(REGIONS))
        ecs = node("ecs-service", "ECS")
        ecs.name = "orders"
        ecs.metadata["ClusterArn"] = "arn:aws:ecs:eu-west-1:123456789012:cluster/apps"
        graph.add_node(ecs)
        page = DRDashboardPage(lambda: "profile", lambda profile, operation: operation(), lambda exc: None)
        page.set_context(graph, "ALL PRODUCTS", TAG)
        metrics, _ = build_metrics([ecs], REGIONS)
        now = datetime.now(timezone.utc)
        results = [MetricResult(metric, "DATA", [(now, 1.0)]) for metric in metrics]
        page._prepared({"context": page.context_key, "profile": "profile", "revision": page._revision,
            "results": results, "regions": REGIONS, "start": now - timedelta(hours=1), "end": now,
            "period": 300, "account": ACCOUNT, "application": "ALL PRODUCTS", "skipped": []})
        states = {control.text(): control.isChecked() for control in page.report_metric_controls.values()}
        self.assertFalse(next(value for label, value in states.items() if "CPU utilization" in label))
        self.assertFalse(next(value for label, value in states.items() if "Memory utilization" in label))
        self.assertTrue(next(value for label, value in states.items() if "Running service tasks" in label))


if __name__ == "__main__":
    unittest.main()
