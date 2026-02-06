import os
import json
import base64
import re
from openai import OpenAI

SCAN_PROMPT = """Analyze this image carefully for ALL receipts. The image may contain MULTIPLE receipts.
FIRST: Count the total number of distinct receipts visible in the image. Look carefully in all areas of the image - receipts may overlap, be at angles, or be partially visible.
THEN: Extract EVERY receipt and EVERY line item from each one. Do NOT skip any receipt.

For each item, provide:
- item_name: the raw text exactly as printed on the receipt
- normalized_name: a clean, human-readable name (e.g., "LG EGGS DZ" -> "Eggs, Large, Dozen")
- category: one of: Dairy, Produce, Meat, Bakery, Beverages, Snacks, Frozen, Household, Fuel, Entree, Appetizer, Dessert, Drink, Side, Clothing, Electronics, Other
- quantity: numeric quantity (default 1)
- unit: one of: each, lb, oz, gal, kg, L (default "each")
- unit_price: price per unit
- line_total: total for this line

For each receipt:
- store_category: one of: Grocery, Restaurant, Gas Station, Retail, Online, Service, Other
- payment_method: one of: Cash, Credit, Debit, Other (if visible)

IMPORTANT: Always return a JSON ARRAY of receipts, even if there is only one receipt.

Return ONLY valid JSON (no markdown, no explanation):
[
  {
    "store_name": "...",
    "store_address": "...",
    "store_category": "...",
    "receipt_date": "YYYY-MM-DD",
    "subtotal": null,
    "tax": null,
    "tip": null,
    "total": 0.00,
    "payment_method": null,
    "items": [
      {
        "item_name": "...",
        "normalized_name": "...",
        "category": "...",
        "quantity": 1,
        "unit": "each",
        "unit_price": 0.00,
        "line_total": 0.00
      }
    ]
  }
]

If you cannot determine a value, use null. For the date, use your best guess from the receipt; if unreadable, use null.
Always include a total for each receipt. Extract every visible line item from every receipt."""


VALID_STORE_CATEGORIES = {"Grocery", "Restaurant", "Gas Station", "Retail", "Online", "Service", "Other"}
VALID_ITEM_CATEGORIES = {
    "Dairy", "Produce", "Meat", "Bakery", "Beverages", "Snacks", "Frozen",
    "Household", "Fuel", "Entree", "Appetizer", "Dessert", "Drink", "Side",
    "Clothing", "Electronics", "Other",
}
VALID_UNITS = {"each", "lb", "oz", "gal", "kg", "L"}


def _resize_image_if_needed(image_path, max_size_mb=15, max_dimension=2048):
    """Resize/compress image if it's too large for the API."""
    from PIL import Image
    import io

    # Check file size
    file_size = os.path.getsize(image_path)
    max_bytes = max_size_mb * 1024 * 1024

    img = Image.open(image_path)

    # Convert to RGB if necessary (handles RGBA, P mode, etc.)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    # Resize if dimensions are too large
    if img.width > max_dimension or img.height > max_dimension:
        ratio = min(max_dimension / img.width, max_dimension / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # Save to buffer and check size
    buffer = io.BytesIO()
    quality = 90
    img.save(buffer, format="JPEG", quality=quality)

    # Reduce quality if still too large
    while buffer.tell() > max_bytes and quality > 30:
        buffer = io.BytesIO()
        quality -= 10
        img.save(buffer, format="JPEG", quality=quality)

    return buffer.getvalue()


def _encode_image(image_path):
    """Encode image to base64, resizing if necessary."""
    try:
        image_bytes = _resize_image_if_needed(image_path)
        return base64.b64encode(image_bytes).decode("utf-8"), "image/jpeg"
    except Exception:
        # Fallback to raw file if PIL fails
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8"), _get_mime_type(image_path)


def _get_mime_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


def _extract_json(text):
    """Extract JSON from the response, handling markdown fences."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _validate_and_clean(data):
    """Validate categories and compute missing fields."""
    if data.get("store_category") not in VALID_STORE_CATEGORIES:
        data["store_category"] = "Other"

    items = data.get("items", [])
    for item in items:
        if item.get("category") not in VALID_ITEM_CATEGORIES:
            item["category"] = "Other"
        if item.get("unit") not in VALID_UNITS:
            item["unit"] = "each"

        line_total = item.get("line_total") or 0
        quantity = item.get("quantity") or 1
        unit_price = item.get("unit_price")

        if not unit_price and quantity and line_total:
            item["unit_price"] = round(line_total / quantity, 2)
        if not line_total and unit_price and quantity:
            item["line_total"] = round(unit_price * quantity, 2)

    return data


def convert_pdf_to_images(pdf_path):
    """Convert each page of a PDF to a PNG image, return list of paths."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    image_paths = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200)
        img_path = pdf_path.rsplit(".", 1)[0] + f"_page{i}.png"
        pix.save(img_path)
        image_paths.append(img_path)
    doc.close()
    return image_paths


def scan_receipt(image_path):
    """Scan a receipt image and return structured data."""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    # Handle PDFs by converting first page to image
    if image_path.lower().endswith(".pdf"):
        pages = convert_pdf_to_images(image_path)
        if not pages:
            return {"error": "Could not convert PDF"}
        image_path = pages[0]  # Use first page

    b64, mime = _encode_image(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": SCAN_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        }
    ]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=16000,
        temperature=0.1,
    )

    raw = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason

    # openai SDK v2.x can return content as a list of content parts
    if isinstance(raw, list):
        parts = []
        for part in raw:
            if isinstance(part, str):
                parts.append(part)
            elif hasattr(part, "text"):
                parts.append(part.text)
            elif isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
        raw = "\n".join(parts)

    if not raw:
        return {"error": "Empty response from AI"}

    # If output was truncated, try to recover by asking GPT to continue
    if finish_reason == "length":
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": "Your response was cut off. Continue the JSON output from exactly where you stopped. Do not repeat what you already wrote."})
        cont = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=16000,
            temperature=0.1,
        )
        cont_raw = cont.choices[0].message.content
        if isinstance(cont_raw, list):
            cont_raw = "\n".join(
                p.text if hasattr(p, "text") else p.get("text", "") if isinstance(p, dict) else str(p)
                for p in cont_raw
            )
        if cont_raw:
            raw = raw + cont_raw

    try:
        data = _extract_json(raw)
    except json.JSONDecodeError:
        # Try to fix truncated JSON by closing open brackets
        fixed = raw.rstrip()
        if not fixed.endswith("]"):
            # Close any open item object, items array, receipt object, and outer array
            if fixed.endswith(","):
                fixed = fixed[:-1]
            open_braces = fixed.count("{") - fixed.count("}")
            open_brackets = fixed.count("[") - fixed.count("]")
            fixed += "}" * max(0, open_braces) + "]" * max(0, open_brackets)
        try:
            data = _extract_json(fixed)
        except json.JSONDecodeError:
            return {"error": "Failed to parse AI response", "raw": raw}

    # Normalize to a list of receipts
    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        return {"error": "Unexpected AI response format"}

    return [_validate_and_clean(receipt) for receipt in data]
