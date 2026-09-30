from manga_ocr import MangaOcr
mocr = MangaOcr()
def get_text(image_path):
    text = mocr(image_path)
    return text

def get_panels():
    """This function will return snipped panels using MAGIv3."""

