import os
import io
import re
import base64
import logging
from openai import OpenAI
import config

logger = logging.getLogger(__name__)

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

def get_openai_client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=config.OPENAI_API_KEY)

def save_upload(file_bytes: bytes, filename: str) -> str:
    """Save an incoming file (image or document) to data/uploads and return its path."""
    clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    filepath = os.path.join(UPLOADS_DIR, clean_name)
    
    # Avoid collisions
    base, ext = os.path.splitext(clean_name)
    counter = 1
    while os.path.exists(filepath):
        filepath = os.path.join(UPLOADS_DIR, f"{base}_{counter}{ext}")
        counter += 1

    with open(filepath, "wb") as f:
        f.write(file_bytes)
        
    logger.info(f"Saved uploaded file to {filepath} ({len(file_bytes)} bytes)")
    return filepath

def analyze_image(image_bytes: bytes, user_prompt: str = None) -> str:
    """
    Analyze image using OpenAI GPT-4o-mini multimodal Vision.
    Extracts text, describes content, reads receipts/diagrams, and answers user queries.
    """
    if not image_bytes:
        return "No image data received."
        
    client = get_openai_client()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    prompt = user_prompt.strip() if user_prompt and user_prompt.strip() else (
        "You are Cedar, an intelligent executive personal assistant. "
        "Analyze this image thoroughly: describe its key elements, transcribe any visible text, "
        "and provide clear executive insights or findings."
    )
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}",
                                "detail": "auto"
                            }
                        }
                    ]
                }
            ],
            max_tokens=750,
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Failed to analyze image with GPT-4o-mini Vision: {e}", exc_info=True)
        return f"Sorry, I encountered an error while analyzing the image: {e}"

def extract_document_text(file_bytes: bytes, filename: str) -> str:
    """
    Extract readable text from a document (PDF, TXT, CSV, Markdown, JSON, etc.).
    """
    ext = os.path.splitext(filename.lower())[1]
    
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages = []
            for idx, page in enumerate(reader.pages):
                txt = page.extract_text()
                if txt:
                    pages.append(f"--- Page {idx + 1} ---\n{txt.strip()}")
            if pages:
                return "\n\n".join(pages)
            return "(PDF document contained no extractable text or is a scanned image.)"
        except Exception as e:
            logger.error(f"Error parsing PDF with pypdf: {e}")
            return f"(Error reading PDF document: {e})"
            
    # Text-based formats
    if ext in [".txt", ".csv", ".json", ".md", ".log", ".py", ".yaml", ".yml", ".html", ".xml"]:
        for encoding in ["utf-8", "latin-1", "cp1252"]:
            try:
                return file_bytes.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
    # Binary or unsupported file
    import uuid
    from datetime import datetime
    corr_id = f"cedar-fb-att-{int(datetime.now().timestamp())}-{uuid.uuid4().hex[:6]}"
    logger.warning(f"[CEDAR_FALLBACK] correlation_id={corr_id} state='UNSUPPORTED_ATTACHMENT' file='{filename}' ext='{ext}' bytes={len(file_bytes)}")
    return f"Attachment '{filename}' is a binary file ({len(file_bytes)} bytes). [State: UNSUPPORTED_ATTACHMENT | Correlation ID: {corr_id}]"

def summarize_document(text: str, filename: str, user_instruction: str = None) -> str:
    """
    Use GPT-4o-mini to summarize or answer questions about an extracted document.
    """
    if not text or text.startswith("("):
        return text
        
    client = get_openai_client()
    instruction = user_instruction.strip() if user_instruction and user_instruction.strip() else (
        "Provide an executive summary of this document, highlighting its main purpose, key points, and action items."
    )
    
    # Truncate document text to ~15,000 chars (~3,500 tokens) for prompt budget
    truncated_text = text[:15000]
    
    prompt = f"""
    You are Cedar, an executive personal assistant.
    The user shared a document named '{filename}'.
    
    User Request: {instruction}
    
    Document Content:
    {truncated_text}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a sharp, helpful executive assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=600,
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        import uuid
        from datetime import datetime
        corr_id = f"cedar-fb-doc-{int(datetime.now().timestamp())}-{uuid.uuid4().hex[:6]}"
        logger.error(f"[CEDAR_FALLBACK] correlation_id={corr_id} state='OUTAGE_OR_RATE_LIMIT' error='{e}'")
        return f"Error analyzing document: {e} [State: OUTAGE_OR_RATE_LIMIT | Correlation ID: {corr_id}]"
