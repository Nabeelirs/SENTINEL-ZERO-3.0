"""View-only selected-cell locator on actual native-grid change colours."""
from io import BytesIO

from PIL import Image, ImageDraw

from ..application.data import ExplorerData, pixel
from .display import comparison


def cell_locator(data: ExplorerData, row: int, column: int) -> bytes:
    """Annotate an in-memory display copy; never mutate a scientific artifact."""
    pixel(data, row, column)  # Existing authoritative index/coordinate validation.
    image = Image.open(BytesIO(comparison(data)[2].png)).convert('RGB')
    draw = ImageDraw.Draw(image)
    for width, colour in ((5, '#08131f'), (2, '#ffffff')):
        draw.line((column-12, row, column-5, row), fill=colour, width=width)
        draw.line((column+5, row, column+12, row), fill=colour, width=width)
        draw.line((column, row-12, column, row-5), fill=colour, width=width)
        draw.line((column, row+5, column, row+12), fill=colour, width=width)
        draw.ellipse((column-4,row-4,column+4,row+4),outline=colour,width=min(width,2))
    output = BytesIO()
    image.save(output,format='PNG',optimize=False)
    return output.getvalue()
