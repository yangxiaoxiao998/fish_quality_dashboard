"""Remove the duplicate seconds suffix from the shared report template."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    template = Path(__file__).resolve().parents[1] / "app" / "templates" / "report_word_template.docx"
    old = b"{{avg_processing_seconds}} \xe7\xa7\x92"
    new = b"{{avg_processing_seconds}}"

    with zipfile.ZipFile(template, "r") as source:
        document_xml = source.read("word/document.xml")
        if old not in document_xml:
            raise RuntimeError("expected avg_processing_seconds suffix not found")
        patched_xml = document_xml.replace(old, new, 1)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as temporary:
            output = Path(temporary.name)
        with zipfile.ZipFile(output, "w") as target:
            for item in source.infolist():
                payload = patched_xml if item.filename == "word/document.xml" else source.read(item.filename)
                target.writestr(item, payload)

    shutil.move(str(output), template)


if __name__ == "__main__":
    main()
