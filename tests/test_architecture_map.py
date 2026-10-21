from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QGraphicsTextItem

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
        self.assertEqual(["DISCOVERY", "ARCHITECTURE MAP", "RESOURCE INVENTORY", "DR DASHBOARD"], tab_titles)
        self.assertEqual("ARCHITECTURE MAP", page.map_detail.currentText())
        self.assertGreater(page.inventory.table.rowCount(), 0)
        self.assertTrue(page.resource_export.isEnabled())

    def test_vpc_aware_architecture_projection_keeps_resources_in_their_vpc(self):
        graph = InfrastructureGraph("123456789012", "test-account", ["eu-west-1"])
        graph.add_node(ResourceNode("vpc-a", None, "VPC", "payments", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("vpc-b", None, "VPC", "reporting", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("subnet-a", None, "Subnet", "private-a", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-a", "AvailabilityZone": "eu-west-1a"}))
        graph.add_node(ResourceNode("subnet-b", None, "Subnet", "private-b", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-b", "AvailabilityZone": "eu-west-1b"}))
        graph.add_node(ResourceNode("ec2-a", None, "EC2", "payments-api", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-a", "SubnetId": "subnet-a", "AvailabilityZone": "eu-west-1a"}))
        graph.add_node(ResourceNode("ec2-b", None, "EC2", "reporting-api", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-b", "SubnetId": "subnet-b", "AvailabilityZone": "eu-west-1b"}))
        graph.add_node(ResourceNode("efs-a", None, "EFS", "payments-data", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-a"}))
        graph.add_edge(ResourceEdge("vpc-a", "subnet-a", "contains"))
        graph.add_edge(ResourceEdge("subnet-a", "ec2-a", "contains"))
        graph.add_edge(ResourceEdge("subnet-b", "ec2-b", "contains"))
        graph.add_edge(ResourceEdge("ec2-a", "efs-a", "attached_to"))

        view = ArchitectureMap()
        groups, edges = view._build_architecture_projection(graph, "ib:resource:application")

        self.assertEqual("vpc-a", groups["architecture::eu-west-1::vpc-a::EC2"]["vpc"])
        self.assertEqual("vpc-b", groups["architecture::eu-west-1::vpc-b::EC2"]["vpc"])
        self.assertEqual("vpc-a", groups["architecture::eu-west-1::vpc-a::EFS"]["vpc"])
        self.assertEqual("attached_to", edges[0]["relation"])
        view.render_graph(graph, detail="ARCHITECTURE MAP")
        self.assertGreater(len(view.scene.items()), 0)

    def test_deployment_map_uses_vpc_az_subnet_boundaries_and_keeps_global_outside_vpc(self):
        graph = InfrastructureGraph("123456789012", "test-account", ["eu-west-1", "global"])
        graph.add_node(ResourceNode("vpc-123", None, "VPC", "platform", "eu-west-1", "123456789012", metadata={"CidrBlock": "10.10.0.0/16"}))
        graph.add_node(ResourceNode("subnet-123", None, "Subnet", "app-private-a", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123", "AvailabilityZone": "eu-west-1a"}))
        graph.add_node(ResourceNode("i-123", None, "EC2", "api-01", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123", "SubnetId": "subnet-123", "AvailabilityZone": "eu-west-1a"}))
        graph.add_node(ResourceNode("s3://audit", None, "S3", "audit", "global", "123456789012"))
        graph.add_edge(ResourceEdge("vpc-123", "subnet-123", "contains"))
        graph.add_edge(ResourceEdge("subnet-123", "i-123", "contains"))

        view = ArchitectureMap()
        view.render_graph(graph, detail="ARCHITECTURE MAP")
        labels = {item.toPlainText() for item in view.scene.items() if isinstance(item, QGraphicsTextItem)}

        self.assertTrue(any(label.startswith("VPC / platform / vpc-123") for label in labels))
        self.assertIn("AVAILABILITY ZONE / eu-west-1a", labels)
        self.assertIn("SUBNET / app-private-a", labels)
        self.assertIn("GLOBAL SERVICES", labels)
        self.assertFalse(any("GLOBAL / UNRESOLVED VPC" in label for label in labels))

    def test_traffic_relationships_do_not_invent_vpc_or_subnet_placement(self):
        graph = InfrastructureGraph("123456789012", "test-account", ["eu-west-1"])
        graph.add_node(ResourceNode("vpc-123", None, "VPC", "platform", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("subnet-123", None, "Subnet", "app-private-a", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123"}))
        graph.add_node(ResourceNode("subnet-456", None, "Subnet", "app-private-b", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123"}))
        graph.add_node(ResourceNode("i-123", None, "EC2", "api-01", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123", "SubnetId": "subnet-123"}))
        graph.add_node(ResourceNode("listener-123", None, "Listener", "HTTPS:443", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("s3-123", None, "S3", "audit", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("vpce-123", None, "VPCEndpoint", "single-subnet", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("alb-123", None, "ALB", "multi-subnet", "eu-west-1", "123456789012"))
        graph.add_edge(ResourceEdge("i-123", "listener-123", "targets"))
        graph.add_edge(ResourceEdge("i-123", "s3-123", "depends_on"))
        graph.add_edge(ResourceEdge("vpce-123", "subnet-123", "attached_to"))
        graph.add_edge(ResourceEdge("alb-123", "subnet-123", "attached_to"))
        graph.add_edge(ResourceEdge("alb-123", "subnet-456", "attached_to"))

        self.assertEqual("vpc-123", ArchitectureMap._node_vpc(graph, graph.nodes["i-123"]))
        self.assertEqual("subnet-123", ArchitectureMap._node_subnet(graph, graph.nodes["i-123"]))
        self.assertEqual("", ArchitectureMap._node_vpc(graph, graph.nodes["listener-123"]))
        self.assertEqual("", ArchitectureMap._node_subnet(graph, graph.nodes["listener-123"]))
        self.assertEqual("", ArchitectureMap._node_vpc(graph, graph.nodes["s3-123"]))
        self.assertEqual("subnet-123", ArchitectureMap._node_subnet(graph, graph.nodes["vpce-123"]))
        self.assertEqual("", ArchitectureMap._node_subnet(graph, graph.nodes["alb-123"]))

    def test_only_a_confirmed_attached_eni_is_rendered_on_its_resource(self):
        graph = InfrastructureGraph("123456789012", "test-account", ["eu-west-1"])
        graph.add_node(ResourceNode("vpc-123", None, "VPC", "platform", "eu-west-1", "123456789012"))
        graph.add_node(ResourceNode("subnet-123", None, "Subnet", "app-private-a", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123", "AvailabilityZone": "eu-west-1a"}))
        graph.add_node(ResourceNode("i-123", None, "EC2", "api-01", "eu-west-1", "123456789012", metadata={"VpcId": "vpc-123", "SubnetId": "subnet-123"}))
        graph.add_node(ResourceNode("eni-attached", None, "NetworkInterface", "eni-attached", "eu-west-1", "123456789012", metadata={"PrivateIp": "10.10.1.20", "AttachmentInstanceId": "i-123"}))
        graph.add_node(ResourceNode("eni-orphan", None, "NetworkInterface", "eni-orphan", "eu-west-1", "123456789012", metadata={"PrivateIp": "10.10.1.21"}))
        graph.add_edge(ResourceEdge("vpc-123", "subnet-123", "contains"))
        graph.add_edge(ResourceEdge("subnet-123", "i-123", "contains"))
        graph.add_edge(ResourceEdge("eni-attached", "i-123", "attached_to"))

        interfaces = ArchitectureMap._attached_interfaces(graph, {"i-123"})
        self.assertEqual(["eni-attached"], [interface.id for interface in interfaces["i-123"]])
        self.assertNotIn("eni-orphan", {interface.id for values in interfaces.values() for interface in values})

        view = ArchitectureMap()
        view.render_graph(graph, detail="ARCHITECTURE MAP")
        labels = {item.toPlainText() for item in view.scene.items() if isinstance(item, QGraphicsTextItem)}
        self.assertIn("ENI eni-attached / 10.10.1.20", labels)
        self.assertFalse(any("eni-orphan" in label for label in labels))
