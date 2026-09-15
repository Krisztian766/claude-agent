import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import render_page  # noqa: E402


def test_render_creates_docs_index_html(tmp_path, monkeypatch):
    monkeypatch.setattr(render_page, "BASE_DIR", tmp_path)
    monkeypatch.setattr(render_page, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render_page, "OUTPUT_FILE", tmp_path / "docs" / "index.html")
    (tmp_path / "STATUS.md").write_text("Alive: yes\nBalance: 1 ETH")
    (tmp_path / "LEARNINGS.md").write_text("- learned something")

    with patch("render_page.subprocess.run") as run_mock:
        run_mock.return_value.stdout = ""
        out_path = render_page.render()

    assert out_path.exists()
    content = out_path.read_text()
    assert "Alive: yes" in content
    assert "learned something" in content


def test_render_escapes_html_in_status(tmp_path, monkeypatch):
    monkeypatch.setattr(render_page, "BASE_DIR", tmp_path)
    monkeypatch.setattr(render_page, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render_page, "OUTPUT_FILE", tmp_path / "docs" / "index.html")
    (tmp_path / "STATUS.md").write_text("<script>alert(1)</script>")
    (tmp_path / "LEARNINGS.md").write_text("fine")

    with patch("render_page.subprocess.run") as run_mock:
        run_mock.return_value.stdout = ""
        out_path = render_page.render()

    content = out_path.read_text()
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content


def test_render_handles_missing_files_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(render_page, "BASE_DIR", tmp_path)
    monkeypatch.setattr(render_page, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render_page, "OUTPUT_FILE", tmp_path / "docs" / "index.html")

    with patch("render_page.subprocess.run") as run_mock:
        run_mock.return_value.stdout = ""
        out_path = render_page.render()

    content = out_path.read_text()
    assert "no status yet" in content
    assert "no learnings yet" in content
