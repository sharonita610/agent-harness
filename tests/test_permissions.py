from pydantic import BaseModel

from harness.tools.permissions import Decision, Policy
from harness.tools.registry import Tier, Tool


class A(BaseModel):
    x: int = 1


def mk(tier):
    return Tool("t_" + tier, "d", A, lambda a: a.x, tier)


def test_default_tiers():
    p = Policy()
    assert p.decide(mk(Tier.READ)) == Decision.ALLOW
    assert p.decide(mk(Tier.WRITE)) == Decision.REQUIRE_APPROVAL
    assert p.decide(mk(Tier.DESTRUCTIVE)) == Decision.DENY


def test_override_by_tool_name():
    p = Policy(tool_overrides={"t_write": Decision.ALLOW})
    assert p.decide(mk(Tier.WRITE)) == Decision.ALLOW


def test_schema_has_no_titles():
    s = mk(Tier.READ).schema()
    assert s["function"]["name"] == "t_read"
    assert "title" not in s["function"]["parameters"]
    assert "x" in s["function"]["parameters"]["properties"]
