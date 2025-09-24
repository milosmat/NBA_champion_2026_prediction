gRPC transport (optional)

1. Install deps:

   - pip install grpcio grpcio-tools

2. Generate stubs:

   - python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto
   - This will create rpc/actor_pb2.py and rpc/actor_pb2_grpc.py

3. Run with gRPC transport:
   - Add flag --transport grpc (we can extend main.py to pass this into ActorSystem).
   - Or set environment USE_GRPC=1 and tweak code to read it.

Notes:

- We intentionally keep payload as raw JSON to reuse existing serialization logic.
- TCP remains default; if gRPC is missing, code prints a helpful error.
