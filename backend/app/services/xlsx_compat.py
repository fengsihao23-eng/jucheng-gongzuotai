"""Preserve workbook data when a WeCom export omits a fill definition."""
import io
from shutil import copyfileobj
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl.xml.functions import SubElement, fromstring, tostring


def repair_empty_fills(archive, content):
    style_name = "xl/styles.xml"
    if style_name not in archive.namelist():
        return content
    styles = fromstring(archive.read(style_name))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    empty_fills = [fill for fill in styles.findall(f"{ns}fills/{ns}fill") if not len(fill)]
    if not empty_fills:
        return content
    for fill in empty_fills:
        SubElement(fill, f"{ns}patternFill", {"patternType": "none"})

    # Keep the fill indices and copy all workbook parts outside the stylesheet intact.
    out = io.BytesIO()
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as repaired:
        for entry in archive.infolist():
            if entry.filename == style_name:
                repaired.writestr(style_name, tostring(styles))
            else:
                with archive.open(entry) as source, repaired.open(entry.filename, "w") as target:
                    copyfileobj(source, target)
    return out.getvalue()
