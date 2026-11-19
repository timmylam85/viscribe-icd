import pytest

from viscribe_api.catalog import CatalogError, read_catalog


def test_duplicate_is_rejected_after_normalizing_code(tmp_path):
    catalog = tmp_path / "duplicate.csv"
    catalog.write_text("code,title_vi\nJ02.9,Viêm họng cấp\nj02.9,Mã bị trùng\n")
    with pytest.raises(CatalogError, match=r"duplicate code J02\.9"):
        read_catalog(catalog)


@pytest.mark.parametrize(
    ("columns", "values", "error"),
    [
        ("age_min", "1", "require a rule_source"),
        ("age_min,age_max,rule_source", "18,10,https://example.com/catalog", "must not exceed"),
        (
            "excludes_codes,rule_source",
            "J00,https://example.com/catalog",
            "excluded codes absent from release",
        ),
        ("active,selectable", "false,true", "inactive code"),
    ],
)
def test_invalid_rules_are_rejected_before_import(tmp_path, columns, values, error):
    catalog = tmp_path / "rules.csv"
    catalog.write_text(f"code,title_vi,{columns}\nJ02.9,Viêm họng cấp,{values}\n")
    with pytest.raises(CatalogError, match=error):
        read_catalog(catalog)
