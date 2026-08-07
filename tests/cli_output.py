from rich.text import Text


def plain_cli_output(output: str) -> str:
    """Remove terminal styling without changing CLI help text."""
    return Text.from_ansi(output).plain
