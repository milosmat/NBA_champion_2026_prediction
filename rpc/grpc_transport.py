import asyncio
import json
from typing import Callable, Awaitable

try:
    import grpc
except Exception:
    grpc = None

try:
    from . import actor_pb2, actor_pb2_grpc
except Exception:
    actor_pb2 = None
    actor_pb2_grpc = None


class GrpcServer:
    def __init__(self, host: str, port: int, handler: Callable[[dict], Awaitable[None]]):
        if grpc is None:
            raise RuntimeError(
                "gRPC transport selected, but grpcio is not installed.\n"
                "Fix: Activate your venv and run:\n"
                "  pip install grpcio grpcio-tools\n"
                "Then generate stubs:\n"
                "  python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto"
            )
        if actor_pb2 is None or actor_pb2_grpc is None:
            raise RuntimeError(
                "gRPC Python stubs not found (rpc/actor_pb2.py, rpc/actor_pb2_grpc.py).\n"
                "Generate them with:\n"
                "  python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto"
            )
        self._host = host
        self._port = port
        self._handler = handler

        class _Servicer(actor_pb2_grpc.ActorServiceServicer):
            def __init__(self, handler: Callable[[dict], Awaitable[None]]):
                self._handler = handler

            async def Send(self, request, context):
                envelope = {
                    "target": request.target,
                    "type": request.type,
                    "payload": json.loads(request.payload_json) if request.payload_json else {},
                }
                await self._handler(envelope)
                return actor_pb2.Ack(ok=True)
        self._servicer_cls = _Servicer
        self._server = grpc.aio.server()

        actor_pb2_grpc.add_ActorServiceServicer_to_server(self._servicer_cls(self._handler), self._server)
        bound = self._server.add_insecure_port(f"{self._host}:{self._port}")
        if bound == 0:
            raise RuntimeError("Failed to bind gRPC server port")
        self._port = bound

    async def start(self):
        await self._server.start()
        return self._host, self._port

    async def stop(self):
        await self._server.stop(0)


async def send_envelope(host: str, port: int, envelope: dict):
    if grpc is None:
        raise RuntimeError(
            "gRPC transport selected, but grpcio is not installed.\n"
            "Fix: Activate your venv and run:\n"
            "  pip install grpcio grpcio-tools\n"
            "Then generate stubs:\n"
            "  python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto"
        )
    if actor_pb2 is None or actor_pb2_grpc is None:
        raise RuntimeError(
            "gRPC Python stubs not found (rpc/actor_pb2.py, rpc/actor_pb2_grpc.py).\n"
            "Generate them with:\n"
            "  python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto"
        )
    async with grpc.aio.insecure_channel(f"{host}:{port}") as channel:
        stub = actor_pb2_grpc.ActorServiceStub(channel)
        payload_json = json.dumps(envelope.get("payload", {}))
        req = actor_pb2.Envelope(target=envelope.get("target", ""), type=envelope.get("type", ""), payload_json=payload_json)
        await stub.Send(req)
