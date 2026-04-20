"""Simple semver-style version comparison for core/adapter compatibility.

Supports:
  - >=1.0.0   - minimum version
  - ^1.0.0     - compatible (1.0.0 to 2.0.0 exclusive based on major)
  - ~1.0.0     - compatible (1.0.x)
  - 1.0.0      - exact version

Does NOT include packaging as a dependency for simplicity.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Version(NamedTuple):
    """Simple version tuple for comparison."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, v: str) -> Version:
        """Parse '1.2.3' or '1.2.3a1' into Version."""
        v = v.strip().lstrip("v")
        match = re.match(r"(\d+)\.(\d+)\.(\d+)", v)
        if not match:
            raise ValueError(f"Invalid version: {v}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_spec(spec: str) -> tuple[str, Version]:
    """Parse a semver spec like '>=1.0.0', '^1.0.0', '~1.0.0', '1.0.0'.

    Returns (operator, version).
    """
    spec = spec.strip()
    if spec.startswith(">="):
        return ">=", Version.parse(spec[2:])
    if spec.startswith("<="):
        return "<=", Version.parse(spec[2:])
    if spec.startswith("^"):
        return "^", Version.parse(spec[1:])
    if spec.startswith("~"):
        return "~", Version.parse(spec[1:])
    if spec.startswith(">"):
        return ">", Version.parse(spec[1:])
    if spec.startswith("<"):
        return "<", Version.parse(spec[1:])
    if spec.startswith("=="):
        return "==", Version.parse(spec[2:])
    # Default: exact
    return "==", Version.parse(spec)


def check_compatibility(spec: str, kernel_version: str) -> bool:
    """Check if kernel_version satisfies the adapter's requires_core spec.

    Args:
        spec: Adapter's requires_core (e.g., '>=0.1.0', '^0.1.0', '0.1.0')
        kernel_version: Kernel's __version__ (e.g., '0.1.0a1')

    Returns:
        True if compatible, False otherwise.
    """
    if not spec:
        return True  # No requirement = compatible with anything

    try:
        kernel_v = Version.parse(kernel_version)
    except ValueError:
        return False  # Invalid kernel version

    try:
        op, required = parse_spec(spec)

        if op == ">=":
            return kernel_v >= required
        if op == ">":
            return kernel_v > required
        if op == "<=":
            return kernel_v <= required
        if op == "<":
            return kernel_v < required
        if op == "==":
            return kernel_v == required
        if op == "^":
            return kernel_v.major == required.major and kernel_v >= required
        if op == "~":
            return (
                kernel_v.major == required.major
                and kernel_v.minor == required.minor
                and kernel_v >= required
            )
    except ValueError:
        return False  # Invalid spec format

    return False


def format_error(spec: str, kernel_version: str) -> str:
    """Format a helpful error message for version mismatch."""
    return (
        f"Adapter requires core {spec} but kernel is version {kernel_version}. "
        f"Upgrade core or use an older adapter version."
    )
