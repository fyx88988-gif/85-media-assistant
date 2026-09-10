from dataclasses import dataclass


@dataclass(frozen=True, order=True, slots=True)
class ProductVersion:
    """Strict three-segment product version used by the local updater."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "ProductVersion":
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError("版本号格式无效。")
        return cls(*(int(part) for part in parts))

