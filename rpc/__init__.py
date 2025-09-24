"""rpc package

Ensures gRPC generated modules import cleanly whether referenced as
package modules (rpc.actor_pb2) or top-level (actor_pb2).

The grpc_tools generated code in actor_pb2_grpc.py uses:
	import actor_pb2 as actor__pb2
which expects a top-level module name. We alias the package submodule
into sys.modules under that top-level name so imports succeed without
editing generated files.
"""

from importlib import import_module
import sys as _sys

try:
	_pkg_actor_pb2 = import_module('.actor_pb2', __name__)
	_sys.modules.setdefault('actor_pb2', _pkg_actor_pb2)
except Exception:
	pass

try:
	# Ensure alias exists before importing grpc stub module
	if 'actor_pb2' not in _sys.modules:
		_pkg_actor_pb2 = import_module('.actor_pb2', __name__)
		_sys.modules.setdefault('actor_pb2', _pkg_actor_pb2)
	_pkg_actor_pb2_grpc = import_module('.actor_pb2_grpc', __name__)
	_sys.modules.setdefault('actor_pb2_grpc', _pkg_actor_pb2_grpc)
except Exception:
	pass
