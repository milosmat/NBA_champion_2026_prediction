import numpy as np
from actor.aggregator import AggregatorP2P
from actor.actor_system import Actor
import pytest

class DummySystem:
    def __init__(self):
        self.sent = []
    def tell(self, actor_name, message):
        self.sent.append((actor_name, type(message).__name__, message))

class DummyMsg:
    pass

class DummyModelShare:
    def __init__(self, coef, intercept):
        self.coef = coef
        self.intercept = intercept

@pytest.mark.asyncio
async def test_fedavg_in_aggregatorp2p(event_loop):
    sys = DummySystem()
    agg = AggregatorP2P("agg", sys)

    # simulate two local models
    from actor.p2p import ModelShare
    await agg.default_behavior(ModelShare("A", np.array([1.0, 3.0]), 0.5))
    await agg.default_behavior(ModelShare("B", np.array([3.0, 5.0]), 1.5))

    # finalize round
    from actor.aggregator import RoundComplete
    await agg.default_behavior(RoundComplete(1, 1, 0.0))

    # one GlobalModel should be sent
    names = [n for (n, t, m) in sys.sent if t == "GlobalModel"]
    assert "evaluator" in names
    # check avg
    gm = [m for (n, t, m) in sys.sent if t == "GlobalModel"][0]
    assert np.allclose(gm.coef, np.array([[2.0, 4.0]]))
    assert abs(gm.intercept - 1.0) < 1e-6
