import pytest
from actor.actor_system import ActorSystem
from actor.p2p import PeerList, StartRound, PeerReady, ModelShare
from actor.evaluator import EvalRequest, EvalReport
from actor.aggregator import SetGlobalModel
import numpy as np

@pytest.mark.asyncio
async def test_serialize_deserialize_messages(event_loop):
    sys = ActorSystem(host="127.0.0.1", port=0)
    await sys.start_network()

    class Sink:
        def __init__(self):
            self.received = []
        async def __call__(self, msg):
            self.received.append(type(msg).__name__)
    
    class A:
        pass

    sys.actors["sink"] = type("X", (), {"mailbox": type("Q", (), {"put_nowait": lambda self, m: None})(), "name": "sink"})
    sys.register_peer("remote", sys.host, sys.port)

    env = sys._serialize("remote", PeerList(["a"], is_reporter=True, reporter_name="p2p_MIA", total_rounds=2))
    assert env["type"] == "PeerList"
    env = sys._serialize("remote", PeerReady("p2p_BOS"))
    assert env["type"] == "PeerReady"
    env = sys._serialize("remote", StartRound())
    assert env["type"] == "StartRound"
    env = sys._serialize("remote", EvalRequest(pairs=None, best_of=7, reply_to=None, round_idx=1))
    assert env["type"] == "EvalRequest"
    env = sys._serialize("remote", EvalReport(results=[{"a":"MIA","b":"BOS"}]))
    assert env["type"] == "EvalReport"
    env = sys._serialize("remote", SetGlobalModel(np.array([[1,2,3]]), 0.1))
    assert env["type"] == "SetGlobalModel"
