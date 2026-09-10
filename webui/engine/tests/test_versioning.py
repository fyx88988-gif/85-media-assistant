import pytest

from media_assistant.versioning import ProductVersion


def test_product_version_orders_each_numeric_segment() -> None:
    assert ProductVersion.parse("1.4.10") > ProductVersion.parse("1.4.2")
    assert ProductVersion.parse("2.0.0") > ProductVersion.parse("1.99.99")


@pytest.mark.parametrize(
    "value",
    ["1.latest.0", "1.0", "1.0.0.0", "v1.0.0", "1.-1.0", ""],
)
def test_product_version_rejects_non_strict_values(value: str) -> None:
    with pytest.raises(ValueError, match="版本号格式无效"):
        ProductVersion.parse(value)

