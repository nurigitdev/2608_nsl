from pathlib import Path
from shutil import copyfile
import subprocess
import tempfile
import unittest

from check_architecture import WORKSPACE, check


PACKAGES = ("nsl-core", "nsl-ir", "nsl-compiler", "nsl-runtime", "nsl-source", "bridge")


class ArchitectureGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.root.mkdir()
        copyfile(WORKSPACE / "rust-toolchain.toml", self.root / "rust-toolchain.toml")

    def workspace(self, additions=None):
        additions = additions or {}
        members = ", ".join(f'"{name}"' for name in PACKAGES)
        (self.root / "Cargo.toml").write_text(
            f'[workspace]\nresolver = "3"\nmembers = [{members}]\n'
            '[workspace.lints.rust]\nunsafe_code = "forbid"\n', encoding="utf-8",
        )
        for name in PACKAGES:
            package = self.root / name
            (package / "src").mkdir(parents=True)
            (package / "src/lib.rs").write_text("", encoding="utf-8")
            (package / "Cargo.toml").write_text(
                f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n'
                f'[lints]\nworkspace = true\n{additions.get(name, "")}', encoding="utf-8",
            )

    def lock(self):
        subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=self.root, check=True)

    def test_ir_can_depend_on_core(self):
        self.workspace({"nsl-ir": '[dependencies]\nnsl-core = {path = "../nsl-core"}\n'})
        self.lock()
        self.assertEqual(check(self.root), [])

    def test_core_rejects_runtime_and_external_path_dependencies(self):
        external = self.root.parent / "sql-driver"
        (external / "src").mkdir(parents=True)
        (external / "src/lib.rs").write_text("", encoding="utf-8")
        (external / "Cargo.toml").write_text(
            '[package]\nname = "sql-driver"\nversion = "0.1.0"\nedition = "2024"\n'
            '[workspace]\n', encoding="utf-8",
        )
        self.workspace({"nsl-core": '[dependencies]\n'
                        'db = {package = "sql-driver", path = "../../sql-driver"}\n'
                        'nsl-runtime = {path = "../nsl-runtime"}\n'})
        self.lock()
        self.assertEqual(check(self.root), [
            "forbidden dependency: nsl-core -> nsl-runtime",
            "forbidden dependency: nsl-core -> sql-driver (non-workspace path)",
        ])

    def test_optional_renamed_target_dependency_is_checked(self):
        self.workspace({"nsl-compiler": "[target.'cfg(target_os = \"none\")'.dependencies]\n"
                        'engine = {package = "nsl-runtime", path = "../nsl-runtime", optional = true}\n'})
        self.lock()
        self.assertEqual(check(self.root), ["forbidden dependency: nsl-compiler -> nsl-runtime"])

    def test_runtime_rejects_transitive_compiler_and_source(self):
        self.workspace({
            "nsl-runtime": '[dependencies]\nbridge = {path = "../bridge"}\n',
            "bridge": '[dependencies]\nnsl-compiler = {path = "../nsl-compiler"}\n',
            "nsl-compiler": '[dependencies]\nnsl-source = {path = "../nsl-source"}\n',
        })
        self.lock()
        self.assertEqual(check(self.root), [
            "forbidden dependency: nsl-runtime -> bridge -> nsl-compiler",
            "forbidden dependency: nsl-runtime -> bridge -> nsl-compiler -> nsl-source",
        ])

    def test_ir_rejects_compiler_layer_and_missing_lint_inheritance(self):
        self.workspace({"nsl-ir": '[build-dependencies]\nnsl-compiler = {path = "../nsl-compiler"}\n'})
        manifest = self.root / "nsl-ir/Cargo.toml"
        manifest.write_text(manifest.read_text().replace("[lints]\nworkspace = true\n", ""), encoding="utf-8")
        self.lock()
        self.assertEqual(check(self.root), [
            "forbidden dependency: nsl-ir -> nsl-compiler",
            "nsl-ir must inherit the workspace lints",
        ])

    def test_compiler_rejects_unsafe_in_an_integration_test(self):
        self.workspace()
        tests = self.root / "nsl-core/tests"
        tests.mkdir()
        (tests / "unsafe_code.rs").write_text(
            '#[test]\nfn rejected() { unsafe { std::ptr::read(&0); } }\n', encoding="utf-8",
        )
        self.lock()
        result = subprocess.run(
            ["cargo", "check", "--locked", "--offline", "-p", "nsl-core", "--tests"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage of an `unsafe` block", result.stderr)


if __name__ == "__main__":
    unittest.main()
