from manga_ocr import MangaOcr
mocr = MangaOcr()
def get_text(image_path):
    text = mocr(image_path)
    return text
