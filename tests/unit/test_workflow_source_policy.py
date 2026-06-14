from agpair.workflows.source_policy import build_source_policy


def test_build_source_policy_is_instructional_not_sandbox_claim() -> None:
    policy = build_source_policy(mode="review", blocked_domains=["example.com"], required_sources=["local evidence"])

    assert policy["mode"] == "review"
    assert policy["blocked_domains"] == ["example.com"]
    assert policy["required_sources"] == ["local evidence"]
    assert policy["enforcement"] == "instruction"
    assert "not a sandbox" in policy["warning"]
