from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox

from pyside6_poc.main import ArchitectureMap, ArchitecturePage
from services.discovery.models import InfrastructureGraph, ResourceEdge, ResourceNode


class ArchitectureMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_projection_aggregates_resources_by_aws_service_type(self):
        graph = InfrastructureGraph("123456789012", "test-account", ["eu-west-1"])
        graph.add_node(ResourceNode("alb", None, "ALB", "orders", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("ec2-a", None, "EC2", "orders-a", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("ec2-b", None, "EC2", "orders-b", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("rds", None, "RDS", "orders-db", "eu-west-1", "123456789012"))
        graph.add_edge(ResourceEdge("alb", "ec2-a", "targets"))
        graph.add_edge(ResourceEdge("alb", "ec2-b", "targets"))
        graph.add_edge(ResourceEdge("ec2-a", "rds", "depends_on"))

        view = ArchitectureMap()
        groups, edges = view._build_projection(graph, "ib:resource:application")
        self.assertEqual(3, len(groups))
        self.assertEqual(2, len(groups["service::EC2"]["members"]))
        self.assertEqual(2, next(edge["count"] for edge in edges if edge["source"] == "service::ALB"))
        view.render_graph(graph)
        self.assertGreater(len(view.scene.items()), 0)

    def test_resource_inventory_tab_remains_available_for_filtered_csv_export(self):
        profile = QComboBox()
        region = QComboBox()
        profile.addItem("engineering")
        region.addItem("eu-west-1")
        page = ArchitecturePage(SimpleNamespace(commands=SimpleNamespace(profile=profile, region=region)), demo=True)

        tab_titles = [page.tabs.tabText(index) for index in range(page.tabs.count())]
        self.assertEqual(["ARCHITECTURE MAP", "RESOURCE INVENTORY"], tab_titles)
        self.assertGreater(page.inventory.table.rowCount(), 0)
        self.assertTrue(page.resource_export.isEnabled())
