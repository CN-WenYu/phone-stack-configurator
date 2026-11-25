"""Build the offline preview with the bundled Three.js runtime."""

from pathlib import Path
from re import sub


ROOT = Path(__file__).resolve().parent


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def script_tag(source, attributes=""):
    # HTML ends a script block even if the closing tag is inside a JS string.
    source = sub(r"</script", lambda match: r"<\/" + match[0][2:], source, flags=2)
    return f"<script{attributes}>\n{source.rstrip()}\n</script>"


def main():
    template = read("src/preview.html")
    vendor = read("vendor/three-r180.js")
    license_text = read("vendor/three-LICENSE.txt")
    sources = {
        "<!-- VENDOR_SCRIPT -->": f"/*\n{license_text.rstrip()}\n*/\n{vendor}",
        "<!-- PREVIEW_SCRIPT -->": read("src/preview.js"),
    }
    for marker, source in sources.items():
        if template.count(marker) != 1:
            raise ValueError(f"Expected exactly one {marker} in the preview template")
        attributes = ' type="module"' if marker == "<!-- PREVIEW_SCRIPT -->" else ""
        template = template.replace(marker, script_tag(source, attributes), 1)
    (ROOT / "index.html").write_text(template, encoding="utf-8")


if __name__ == "__main__":
    main()
