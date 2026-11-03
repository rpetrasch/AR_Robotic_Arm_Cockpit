"""Run the cockpit agent: the ROS node on a background executor, the HTTP API on uvicorn.

    ros2 run ar3_cockpit_agent cockpit_agent --ros-args -p mode:=sim

Started by the bringup (``cockpit_agent:=true``, the default). Environment:

- ``COCKPIT_AGENT_TOKEN``: shared secret with the cockpit backend (required);
- ``COCKPIT_AGENT_BIND`` (default 127.0.0.1) and ``COCKPIT_AGENT_PORT`` (default 8790);
- ``COCKPIT_REMOTE_MOTION=1``: remote clients (the Mac in REMOTE mode) may send motion.
"""

from __future__ import annotations

import os
import sys
import threading

import rclpy
import uvicorn
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.signals import SignalHandlerOptions

from .app import create_app
from .node import AgentNode


def main(argv=None) -> int:
    # uvicorn owns SIGINT/SIGTERM; when it returns, ROS is shut down below.
    rclpy.init(
        args=argv if argv is not None else sys.argv, signal_handler_options=SignalHandlerOptions.NO
    )
    node = AgentNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    def spin():
        try:
            executor.spin()
        except ExternalShutdownException:
            pass

    thread = threading.Thread(target=spin, name="ros", daemon=True)
    thread.start()
    node.sampler.start()
    host = os.environ.get("COCKPIT_AGENT_BIND", "127.0.0.1")
    port = int(os.environ.get("COCKPIT_AGENT_PORT", "8790"))
    token = os.environ.get("COCKPIT_AGENT_TOKEN", "")
    if not token:
        node.get_logger().error("COCKPIT_AGENT_TOKEN is not set: every request gets 503")
    node.get_logger().info(f"cockpit agent ({node.mode}) on http://{host}:{port}")
    try:
        # wsproto: python3-uvicorn depends on it on Ubuntu 24.04
        # (python3-websockets is not in the image).
        uvicorn.run(
            create_app(node, token), host=host, port=port, log_level="warning", ws="wsproto"
        )
    finally:
        node.sampler.stop()
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        thread.join(timeout=2)
    return 0
