import pytest

from tests.cli_output import plain_cli_output


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("plain --project-id\n", "plain --project-id\n"),
        ("\x1b[1;36m-\x1b[0m\x1b[1;36m-project-id\x1b[0m", "--project-id"),
        (
            "\x1b[1m-\x1b[0m\x1b[36m-project\x1b[0m\x1b[33m-id\x1b[0m",
            "--project-id",
        ),
        ("╭─ Привет 🚀\n  --yes  ", "╭─ Привет 🚀\n  --yes  "),
        (
            "\x1b]8;;https://example.test\x1b\\--project-id\x1b]8;;\x1b\\",
            "--project-id",
        ),
        ("before\x1b[31", "before\x1b[31"),
    ],
)
def test_plain_cli_output_preserves_semantics(output: str, expected: str) -> None:
    assert plain_cli_output(output) == expected
