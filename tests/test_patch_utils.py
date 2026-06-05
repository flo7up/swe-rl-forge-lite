from __future__ import annotations

from pathlib import Path

from forge.patch_utils import apply_patch_file, check_patch_applies


def test_patch_application_helper_on_tiny_fixture(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    patch = tmp_path / "change.patch"
    patch.write_text(
        """diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
""",
        encoding="utf-8",
    )

    assert check_patch_applies(tmp_path, patch)
    result = apply_patch_file(tmp_path, patch)

    assert result.success
    assert target.read_text(encoding="utf-8") == "new\n"