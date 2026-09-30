from manga_ocr import MangaOcr
mocr = MangaOcr()
def get_text(image_path):
    text = mocr(image_path)
    return text

def get_panels():

"""THis function returns snipped panels using the magiV3"""

