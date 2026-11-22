"""HP 111–117 Windows profile in 300/in grid units; dimensions are the physical tray sizes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# PPD name, Chinese label, width/height mm, SPL3 size, Windows header width/height, Windows ID, PWG
PAPERS = {
    "A4": ("A4", 210, 297, 2, 2480, 3507, 9, "iso_a4_210x297mm"),
    "Letter": ("Letter", 215.9, 279.4, 0, 2550, 3300, 1, "na_letter_8.5x11in"),
    "Legal": ("Legal", 215.9, 355.6, 1, 2550, 4200, 5, "na_legal_8.5x14in"),
    "A5": ("A5", 148, 210, 16, 1748, 2480, 11, "iso_a5_148x210mm"),
    "A5LEF": ("A5 长边进纸", 210, 148, 21, 2480, 1748, 11, "om_a5-lef_210x148mm"),
    "B5": ("JIS B5", 182, 257, 11, 2149, 3035, 13, "jis_b5_182x257mm"),
    "Executive": ("Executive", 184.15, 266.7, 3, 2175, 3150, 7, "na_executive_7.25x10.5in"),
    "Folio": ("Folio 8.5 × 13 英寸", 215.9, 330.2, 24, 2550, 3900, 14, "na_foolscap_8.5x13in"),
    "Oficio": ("Oficio 8.5 × 13.5 英寸", 215.9, 342.9, 28, 2550, 4050, 190, "na_oficio_8.5x13.5in"),
    "Env10": ("10 号信封", 104.775, 241.3, 6, 1237, 2850, 20, "na_number-10_4.125x9.5in"),
    "Monarch": ("Monarch 信封", 98.425, 190.5, 7, 1162, 2250, 37, "na_monarch_3.875x7.5in"),
    "DL": ("DL 信封", 110, 220, 9, 1299, 2598, 27, "iso_dl_110x220mm"),
    "C5": ("C5 信封", 162, 229, 8, 1913, 2704, 28, "iso_c5_162x229mm"),
    "Postcard": ("4 × 6 英寸卡片", 101.6, 152.4, 14, 1200, 1800, 130, "na_index-4x6_4x6in"),
    "roc16k": ("16K", 195, 270, 35, 2303, 3189, 194, "om_16k_195x270mm"),
}
MEDIA = {"OFF":"打印机默认","NORMAL":"普通纸","THICK":"厚纸 90–120 g/m²","THIN":"薄纸 60–69 g/m²",
         "BOND":"证券纸","COLOR":"彩色纸","CARD":"卡纸 121–163 g/m²","LABEL":"标签纸",
         "ENV":"信封","USED":"预印纸","RECYCLED":"再生纸"}
GRID = {1:(1,1), 2:(2,1), 4:(2,2), 6:(3,2), 9:(3,3), 16:(4,4)}
PT = 72 / 25.4
def dimensions(s):
    if s["paper"] == "Custom":
        w, h = float(s["width"]), float(s["height"])
        if not (76 <= w <= 216 and 127 <= h <= 356):
            raise ValueError("自定义纸宽须为 76–216 mm，长须为 127–356 mm。")
        return w, h
    return PAPERS[s["paper"]][1:3]

def paper_default(width, height):
    for name, (_, w, h, *_) in PAPERS.items():
        if abs(width - w) < .7 and abs(height - h) < .7:
            return name
    return "Custom"
