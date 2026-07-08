from pathlib import Path

from app.modules.company_import import parse_company_csv


def write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "companies.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_valid_csv(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "name,website,country,city,industry,status,notes\n"
        "Acme,https://acme.example,US,Austin,Software,ACTIVE,Priority account\n",
    )

    result = parse_company_csv(path)

    assert result.errors == []
    assert len(result.rows) == 1
    assert result.rows[0].row_number == 2
    assert result.rows[0].name == "Acme"
    assert result.rows[0].website == "https://acme.example"
    assert result.rows[0].country == "US"
    assert result.rows[0].city == "Austin"
    assert result.rows[0].industry == "Software"
    assert result.rows[0].status == "ACTIVE"
    assert result.rows[0].notes == "Priority account"


def test_missing_name_column_returns_file_error(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "website,country\nhttps://acme.example,US\n")

    result = parse_company_csv(path)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].row_number is None
    assert result.errors[0].message == "Missing required column: name."


def test_empty_name_returns_row_error(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,website\n,https://acme.example\n")

    result = parse_company_csv(path)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].row_number == 2
    assert result.errors[0].message == "Company name is required."


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,website\n\nAcme,\n,\n\nGlobex,https://globex.example\n")

    result = parse_company_csv(path)

    assert result.errors == []
    assert [row.name for row in result.rows] == ["Acme", "Globex"]
    assert [row.row_number for row in result.rows] == [3, 6]


def test_empty_fields_are_normalized_to_none(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "name,website,country,city,industry,notes\nAcme,, ,,,\n",
    )

    result = parse_company_csv(path)

    row = result.rows[0]
    assert row.website is None
    assert row.country is None
    assert row.city is None
    assert row.industry is None
    assert row.notes is None


def test_missing_optional_columns_are_allowed(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name\nAcme\n")

    result = parse_company_csv(path)

    assert result.errors == []
    assert result.rows[0].name == "Acme"
    assert result.rows[0].website is None
    assert result.rows[0].country is None
    assert result.rows[0].city is None
    assert result.rows[0].industry is None
    assert result.rows[0].notes is None


def test_empty_or_missing_status_defaults_to_new(tmp_path: Path) -> None:
    empty_status_path = write_csv(tmp_path, "name,status\nAcme,\n")
    missing_status_path = tmp_path / "companies_without_status.csv"
    missing_status_path.write_text("name\nGlobex\n", encoding="utf-8")

    empty_status_result = parse_company_csv(empty_status_path)
    missing_status_result = parse_company_csv(missing_status_path)

    assert empty_status_result.rows[0].status == "NEW"
    assert missing_status_result.rows[0].status == "NEW"


def test_extra_columns_are_ignored(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,unknown\nAcme,ignored value\n")

    result = parse_company_csv(path)

    assert result.errors == []
    assert result.rows[0].name == "Acme"
    assert not hasattr(result.rows[0], "unknown")


def test_extra_positional_value_returns_row_error(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,website\nAcme,example.com,unexpected\n")

    result = parse_company_csv(path)

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].row_number == 2
    assert result.errors[0].message == "Malformed CSV row: unexpected extra values."


def test_utf8_company_names(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,country\n株式会社みらい,日本\nCafé Étoile,France\n")

    result = parse_company_csv(path)

    assert result.errors == []
    assert [row.name for row in result.rows] == ["株式会社みらい", "Café Étoile"]
