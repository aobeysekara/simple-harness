"""Check the current build's metadata, licence and distribution boundaries."""

import tarfile
import tomllib
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def require(condition: bool, message: str) -> None:
    """Fail a release check even when Python assertions are disabled."""
    if not condition:
        raise ValueError(message)


def check_members(names: list[str]) -> None:
    """Reject evidence and common local data paths in a distribution."""
    for name in names:
        parts = PurePosixPath(name).parts
        forbidden = (
            "evidence" in parts
            or any(p in {".git", ".venv", ".simple", "__pycache__"} for p in parts)
            or any(p.startswith(".env") for p in parts)
            or name.endswith((".token", ".sqlite", ".sqlite3", ".pyc"))
            or ".sqlite-" in parts[-1]
            or ".sqlite3-" in parts[-1]
        )
        require(not forbidden, f"Local data or experiment evidence packaged: {name}")


def check_metadata(content: bytes, name: str, version: str) -> None:
    """Check the same release identity in wheel and source metadata."""
    metadata = BytesParser().parsebytes(content)
    expected = {"Name": name, "Version": version, "License-Expression": "MIT"}
    for field, value in expected.items():
        require(metadata[field] == value, f"Invalid distribution metadata: {field}")
    require(
        metadata.get_all("License-File") == ["LICENSE"],
        "Invalid distribution metadata: licence file",
    )
    require(
        "Development Status :: 3 - Alpha" in metadata.get_all("Classifier", []),
        "Invalid distribution metadata: alpha status",
    )


def main() -> None:
    """Validate the wheel and source archive built from this checkout."""
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    version = project["version"]
    prefix = f"simple_framework-{version}"
    licence = Path("LICENSE").read_bytes()
    with ZipFile(Path("dist") / f"{prefix}-py3-none-any.whl") as wheel:
        check_members(wheel.namelist())
        require("simple_framework/py.typed" in wheel.namelist(), "Missing wheel typing marker")
        check_metadata(wheel.read(f"{prefix}.dist-info/METADATA"), project["name"], version)
        require(
            wheel.read(f"{prefix}.dist-info/licenses/LICENSE") == licence, "Wheel licence differs"
        )
    with tarfile.open(Path("dist") / f"{prefix}.tar.gz") as archive:
        check_members(archive.getnames())
        metadata_file = archive.extractfile(f"{prefix}/PKG-INFO")
        if metadata_file is None:
            raise ValueError("Missing source metadata")
        check_metadata(metadata_file.read(), project["name"], version)
        for relative in (
            "LICENSE",
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CHANGELOG.md",
            "docs/alpha-release.md",
            "examples/reading_group.py",
            "src/simple_framework/py.typed",
        ):
            member = archive.extractfile(f"{prefix}/{relative}")
            if member is None:
                raise ValueError(f"Missing source file: {relative}")
            require(
                member.read() == Path(relative).read_bytes(), f"Source file differs: {relative}"
            )
    print(f"PASS {version}: licence, metadata, typing marker and distribution boundaries")


if __name__ == "__main__":
    main()
