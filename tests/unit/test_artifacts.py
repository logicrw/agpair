from pathlib import Path

from agpair.artifacts import read_excerpt


def test_read_excerpt_preserves_head_and_tail_for_long_output(tmp_path: Path) -> None:
    output = tmp_path / "stdout.log"
    output.write_text("HEAD-" + ("x" * 120) + "-TAIL", encoding="utf-8")

    excerpt = read_excerpt(output, max_chars=60)

    assert excerpt is not None
    assert excerpt.startswith("HEAD-")
    assert "[... truncated by agpair ...]" in excerpt
    assert excerpt.endswith("-TAIL")
    assert len(excerpt) <= 60
