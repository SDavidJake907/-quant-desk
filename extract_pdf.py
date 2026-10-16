from pypdf import PdfReader
import sys

pdf_path = "C:\\Users\\kitti\\Downloads\\VAL-CALENDAR.pdf"
reader = PdfReader(pdf_path)
print(f"Pages: {len(reader.pages)}")
for i, page in enumerate(reader.pages):
    print(f"\n--- Page {i+1} ---")
    text = page.extract_text()
    if text:
        print(text[:5000])
    else:
        print("(no text extracted)")
