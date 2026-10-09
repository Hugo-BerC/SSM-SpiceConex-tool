from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pyside6_poc.main import ArchitectureMap
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
