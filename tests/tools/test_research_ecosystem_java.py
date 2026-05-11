"""Tests for the Java ecosystem plugin."""

from pathlib import Path

from tools.research.ecosystems import java as java_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = java_plugin.plugin.match(fixture_path("java", "happy"))
    assert record is not None
    assert record.name == "java"
    assert "pom.xml" in record.manifest_paths


def test_declared_deps_happy_includes_artifacts() -> None:
    deps = java_plugin.plugin.declared_deps(fixture_path("java", "happy"))
    assert "spring_core" in deps
    assert "junit_jupiter" in deps


def test_scan_imports_happy_finds_imports() -> None:
    imports = java_plugin.plugin.scan_imports(fixture_path("java", "happy"))
    assert "org.springframework.context.applicationcontext" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = java_plugin.plugin.match(fixture_path("java", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = java_plugin.plugin.match(fixture_path("java", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert java_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = java_plugin.plugin.standard_dirs()
    assert "src/test/java/" in dirs["test"]
    assert "src/main/java/" in dirs["source"]


def test_manifest_paths_lists_canonical_filenames() -> None:
    paths = java_plugin.plugin.manifest_paths()
    assert paths == ("pom.xml", "build.gradle", "build.gradle.kts")


def test_declared_deps_gradle_groovy_extracts_artifact(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text(
        """
        dependencies {
            implementation 'com.google.guava:guava:32.0.0-jre'
            testImplementation "org.junit.jupiter:junit-jupiter:5.10.0"
            api 'singlepart-coord'
        }
        """,
        encoding="utf-8",
    )
    deps = java_plugin.plugin.declared_deps(tmp_path)
    assert "guava" in deps
    assert "junit_jupiter" in deps
    # Single-segment coord (no colons) falls through to the else branch.
    assert "singlepart_coord" in deps


def test_declared_deps_gradle_kts_also_supported(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text(
        """
        dependencies {
            implementation("org.jetbrains.kotlin:kotlin-stdlib:1.9.0")
        }
        """,
        encoding="utf-8",
    )
    deps = java_plugin.plugin.declared_deps(tmp_path)
    assert "kotlin_stdlib" in deps


def test_declared_deps_pom_without_namespace_extracts_artifacts(tmp_path: Path) -> None:
    # No xmlns on <project> — exercises the namespace-strip branch where
    # ``"}"`` is not in elem.tag for any element.
    (tmp_path / "pom.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>my-app</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.apache.commons</groupId>
      <artifactId>commons-lang3</artifactId>
      <version>3.14.0</version>
    </dependency>
  </dependencies>
</project>
""",
        encoding="utf-8",
    )
    deps = java_plugin.plugin.declared_deps(tmp_path)
    assert "commons_lang3" in deps
    # Project's own artifactId ("my-app") must be skipped.
    assert "my_app" not in deps
